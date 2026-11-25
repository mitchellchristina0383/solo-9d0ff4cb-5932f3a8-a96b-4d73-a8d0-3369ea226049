"""Sample Compose backend: identifies itself, demonstrates mounts and delays.

The application is written as if it always runs at its own origin root.  All
form actions and internal links are relative references with no host name or
leading mount prefix, so the same app works directly (``/``) and underneath
any number of gateway mount sub-paths without configuration:

* ``GET /``          HTML demo page with a relative form and redirect demos
* ``POST /``         PRG endpoint that 302-redirects to a relative result page
* ``GET  /result``   result page rendered after the form redirect
* ``GET  /redirect/...``  relative / absolute / off-site Location demos
* everything else    JSON echo used by the existing probe behaviour
"""

from __future__ import annotations

import json
import logging
import os
import time

from flask import Flask, Response, request, url_for

app = Flask(__name__)
SERVICE_NAME = os.environ.get("SERVICE_NAME", "sample-a")
SERVICE_COLOR = os.environ.get("SERVICE_COLOR", "blue")
MAX_DELAY_MS = 120_000
ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]


def _delay_ms() -> int:
    try:
        delay_ms = int(request.args.get("delay_ms", "0"))
    except ValueError:
        delay_ms = 0
    return max(0, min(delay_ms, MAX_DELAY_MS))


def _page(title: str, body: str) -> Response:
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · {SERVICE_NAME}</title>
<style>
  body {{ font: 15px/1.6 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; background: #f5f6f8; color: #1c2430; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 28px 20px 60px; }}
  .card {{ background: #fff; border: 1px solid #d8dee8; border-radius: 10px;
           padding: 20px 22px; margin-bottom: 18px; }}
  h1 {{ font-size: 19px; margin: 0 0 4px; }}
  h2 {{ font-size: 15px; margin: 18px 0 8px; }}
  .muted {{ color: #667085; font-size: 13px; }}
  .pill {{ display: inline-block; padding: 2px 10px; border-radius: 999px;
           background: #eef2f7; font-size: 12px; font-weight: 600; }}
  label {{ display: block; font-size: 13px; color: #667085; margin: 10px 0 4px; }}
  input {{ width: 100%; max-width: 320px; padding: 8px 10px; border: 1px solid #d8dee8;
           border-radius: 7px; font: inherit; }}
  button {{ background: #1f6feb; color: #fff; border: 0; padding: 9px 20px;
            border-radius: 7px; font: inherit; font-weight: 600; cursor: pointer;
            margin-top: 14px; }}
  code {{ background: #f2f4f7; padding: 1px 5px; border-radius: 4px; }}
  ul {{ margin: 6px 0; padding-left: 20px; }}
</style>
</head>
<body>
<main>
  <section class="card">
    <h1>{title}</h1>
    <p class="muted">服务身份 <span class="pill">{SERVICE_NAME}</span>
       颜色 <span class="pill">{SERVICE_COLOR}</span>
       当前主机 <code>{request.host}</code></p>
    {body}
  </section>
</main>
</body>
</html>
"""
    return Response(html, mimetype="text/html; charset=utf-8")


@app.get("/")
def index() -> Response:
    delay_ms = _delay_ms()
    if delay_ms:
        time.sleep(delay_ms / 1000.0)
    # No action attribute: the form posts back to the *current* URL, which is
    # the mount entry whether or not it carries a trailing slash.  All other
    # links below are relative references as well.
    body = """
    <p class="muted">
      本页所有地址都是相对地址，不包含网关挂载前缀；无论应用挂在根目录还是网关子路径，
      都可以直接打开和提交。
    </p>
    <form method="post">
      <label for="name">留言内容（提交后经 302 跳到结果页）</label>
      <input id="name" name="name" value="你好，网关">
      <button type="submit">相对地址提交</button>
    </form>
    <h2>Location 重写演示</h2>
    <ul>
      <li><a href="redirect/relative?say=hi#sec1">相对 Location（保留查询串和锚点）</a></li>
      <li><a href="redirect/dot?say=dot">点段相对 Location（result 同级）</a></li>
      <li><a href="redirect/absolute?say=abs">绝对 Location（指回本后端）</a></li>
      <li><a href="redirect/offsite">站外跳转（网关原样返回）</a></li>
    </ul>
    <h2>其它</h2>
    <ul>
      <li><a href="healthz">healthz</a></li>
      <li><a href="echo/anything?delay_ms=0">JSON 回声接口 <code>/echo/...</code></a></li>
    </ul>
    """
    return _page("挂载演示应用", body)


@app.post("/")
def submit_root() -> Response:
    # The action-less form posts to the mount entry itself, which the upstream
    # sees as POST "/".  Answer with PRG and a *relative* Location carrying the
    # submitted value as a query string plus a fragment.  The gateway rebases
    # it under the mount; with two mounts the browser stays on the entry used.
    name = request.form.get("name", "")
    response = Response(status=302)
    response.headers["Location"] = "result?submitted=" + name + "#done"
    return response


@app.get("/result")
def result() -> Response:
    submitted = request.args.get("submitted", "")
    body = f"""
    <h2 id="done">提交结果</h2>
    <p>收到留言：<strong>{submitted}</strong></p>
    <p class="muted">查询串 <code>{request.query_string.decode() or "(空)"}</code>
       由后端重定向带回，锚点 <code>#done</code> 保留在浏览器侧。</p>
    <p><a href="./">返回表单页（相对地址）</a></p>
    """
    return _page("提交结果", body)


@app.get("/redirect/relative")
def redirect_relative() -> Response:
    # Relative reference with a path; query and fragment travel along.
    response = Response(status=302)
    location = "../result?came=relative"
    if request.query_string:
        location += "&" + request.query_string.decode()
    response.headers["Location"] = location + "#frag"
    return response


@app.get("/redirect/dot")
def redirect_dot() -> Response:
    # Same-directory reference, another relative shape the gateway must rebase.
    response = Response(status=302)
    location = "result?came=dot"
    if request.query_string:
        location += "&" + request.query_string.decode()
    response.headers["Location"] = location
    return response


@app.get("/redirect/absolute")
def redirect_absolute() -> Response:
    # Absolute URL pointing at this very backend.  The gateway must rewrite it
    # to the public mounted URL instead of leaking the in-network origin.
    response = Response(status=302)
    location = url_for("result", _external=True) + "?came=absolute"
    if request.query_string:
        location += "&" + request.query_string.decode()
    response.headers["Location"] = location + "#abs"
    return response


@app.get("/redirect/offsite")
def redirect_offsite() -> Response:
    # A different origin: the gateway returns the Location verbatim.
    response = Response(status=302)
    response.headers["Location"] = "https://example.com/external?keep=1#ext"
    return response


@app.route("/echo/", defaults={"path": ""}, methods=ALL_METHODS)
@app.route("/echo/<path:path>", methods=ALL_METHODS)
def echo(path: str) -> Response:
    delay_ms = _delay_ms()
    started = time.monotonic()
    if delay_ms:
        time.sleep(delay_ms / 1000.0)
    waited_ms = int((time.monotonic() - started) * 1000)
    payload = {
        "service": SERVICE_NAME,
        "color": SERVICE_COLOR,
        "method": request.method,
        "path": "/echo/" + path,
        "query": request.args.to_dict(flat=False),
        "requested_delay_ms": delay_ms,
        "waited_ms": waited_ms,
        "host": request.host,
        "headers": {
            key: value
            for key, value in request.headers.items()
            if key.lower().startswith("x-")
        },
        "body": request.get_data(as_text=True)[:2000],
    }
    response = Response(
        json.dumps(payload, ensure_ascii=False) + "\n",
        status=200,
        mimetype="application/json",
    )
    response.headers["X-Sample-Service"] = SERVICE_NAME
    response.headers["X-Sample-Color"] = SERVICE_COLOR
    return response


@app.get("/healthz")
def healthz() -> Response:
    return Response(
        json.dumps({"status": "ok", "service": SERVICE_NAME}),
        mimetype="application/json",
    )


if __name__ == "__main__":
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "9000")))
