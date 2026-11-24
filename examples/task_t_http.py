# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the License); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an AS IS BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from urllib.request import Request, urlopen

from openan_observability_sdk import LogSeverity, ObservabilitySdk, SdkConfig, SignalKind, TraceContextPropagation


def attributes(run_id: str) -> dict[str, str]:
    return {"extension.name": "task-t", "task.type": "diagnosis", "task.object": run_id}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        with TraceContextPropagation.extract_to_current(self.headers):
            with observe.trace("SendMessage", SignalKind.SERVER, attributes(run_id)) as span:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                observe.metric("task.count", 1, {"task.type": "diagnosis"})
                observe.log(
                    "gen_ai.agent.a2at.message",
                    LogSeverity.INFO,
                    "Task-T request completed",
                    attributes(run_id),
                    span=span,
                )
                span.ok()
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")


config = SdkConfig(service_name="task-t-example")
with ObservabilitySdk.start(config) as sdk:
    observe = sdk.observe()
    run_id = "local-run"
    server = HTTPServer(("127.0.0.1", 8088), Handler)
    thread = Thread(target=server.handle_request, daemon=True)
    thread.start()

    with observe.trace("SendMessage", SignalKind.CLIENT, attributes(run_id)) as span:
        with span.make_current():
            headers: dict[str, str] = {}
            TraceContextPropagation.inject(headers)
            request = Request("http://127.0.0.1:8088/", data=b"task", headers=headers, method="POST")
            with urlopen(request, timeout=5) as response:
                response.read()
        span.ok()

    thread.join(timeout=5)
    server.server_close()
    if not sdk.flush(timeout=10):
        raise RuntimeError("OTLP flush failed or timed out")
