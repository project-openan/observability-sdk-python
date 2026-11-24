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

from openan_observability_sdk import LogSeverity, ObservabilitySdk, SignalKind


with ObservabilitySdk.start() as sdk:
    observe = sdk.observe()
    workflow = {
        "workflow.id": "wf-001",
        "workflow.type": "fault-diagnosis",
        "workflow.status": "running",
    }

    with observe.trace("a2at.workflow", SignalKind.INTERNAL, workflow) as workflow_span:
        observe.metric(
            "workflow.duration",
            42.0,
            {"workflow.type": "fault-diagnosis", "workflow.status": "completed"},
        )
        observe.metric(
            "workflow.count",
            1,
            {"workflow.type": "fault-diagnosis", "workflow.status": "completed"},
        )
        observe.log(
            "gen_ai.agent.a2at.workflow",
            LogSeverity.INFO,
            "workflow completed",
            {**workflow, "workflow.status": "completed"},
            span=workflow_span,
        )
        workflow_span.ok()

    agent = {
        "gen_ai.agent.name": "diagnosis-agent",
        "agent.role": "diagnostician",
        "agent.skills": ["alarm-analysis", "fault-localization"],
        "agent.state": "completed",
    }
    observe.log("gen_ai.agent.a2at.agent", LogSeverity.INFO, "agent completed", agent)

    substep = {
        "gen_ai.agent.name": "diagnosis-agent",
        "substep.name": "retrieve-alarms",
        "substep.status": "completed",
    }
    with observe.trace("a2at.substep.retrieve-alarms", SignalKind.INTERNAL, substep) as substep_span:
        observe.metric(
            "substep.duration",
            50.0,
            {"gen_ai.agent.name": "diagnosis-agent", "substep.name": "retrieve-alarms"},
        )
        observe.metric(
            "gen_ai.client.token.usage",
            120,
            {
                "gen_ai.agent.name": "diagnosis-agent",
                "gen_ai.request.model": "model-1",
                "gen_ai.token.type": "input",
            },
        )
        observe.log("gen_ai.agent.a2at.substep", LogSeverity.INFO, "substep completed", substep, span=substep_span)
        substep_span.ok()

    if not sdk.flush(timeout=10):
        raise RuntimeError("OTLP flush failed or timed out")
