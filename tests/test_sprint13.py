from __future__ import annotations

import asyncio

from browser_use.agent import Agent, AgentHistory, AgentHistoryList, AgentOutput, MessageManager
from browser_use.agent.controller import Controller, ControllerState, StepResult
from browser_use.agent.planner import Plan, PlanStep, Planner, PlanningContext
from browser_use.browser.views import BrowserStateSummary
from browser_use.llm.base import BaseChatModel


def test_planner_exposes_structured_models() -> None:
    async def run() -> Plan:
        state = BrowserStateSummary(
            url="file:///tmp/sprint13-planner.html",
            title="Planner Page",
            elements=[{"index": 0, "tag": "button", "text": "Continue"}],
        )
        history = AgentHistoryList(
            histories=[
                AgentHistory(
                    model_output=AgentOutput(evaluation="loaded start page", next_goal="continue"),
                    state=state,
                )
            ]
        )
        return await Planner().decompose(
            PlanningContext(task="click continue and finish", browser_state=state, history=history)
        )

    plan = asyncio.run(run())

    assert isinstance(plan, Plan)
    assert plan.task == "click continue and finish"
    assert isinstance(plan.steps[0], PlanStep)
    assert plan.steps[0].sub_goal
    assert plan.steps[0].expected_state
    assert plan.steps[0].fallback_strategy in {"retry", "skip", "fallback", "abort", "replan"}
    assert plan.model_dump()["steps"][0]["sub_goal"] == plan.steps[0].sub_goal


def test_controller_executes_and_records_transitions() -> None:
    class RecordingTools:
        def __init__(self) -> None:
            self.actions: list[dict[str, object]] = []

        async def execute_action(self, action: dict[str, object], browser_session: object | None = None) -> dict[str, object]:
            self.actions.append(action)
            return {"ok": True, "url": "file:///tmp/sprint13-controller.html", "text": "finished"}

    async def run() -> tuple[Controller, RecordingTools]:
        tools = RecordingTools()
        plan = Plan(
            task="finish task",
            steps=[
                PlanStep(
                    sub_goal="open page",
                    expected_state="url contains sprint13-controller",
                    action={"navigate": {"url": "file:///tmp/sprint13-controller.html"}},
                    max_retries=0,
                ),
                PlanStep(
                    sub_goal="finish",
                    expected_state="text contains finished",
                    action={"done": {"success": True, "text": "finished"}},
                    max_retries=0,
                ),
            ],
        )
        controller = Controller(tools=tools, browser_session=object())
        result = await controller.execute_plan(plan)
        assert result.success is True
        return controller, tools

    controller, tools = asyncio.run(run())

    assert [entry.state for entry in controller.transition_history] == [
        ControllerState.PLANNING,
        ControllerState.EXECUTING,
        ControllerState.VERIFYING,
        ControllerState.EXECUTING,
        ControllerState.VERIFYING,
        ControllerState.DONE,
    ]
    assert tools.actions == [
        {"navigate": {"url": "file:///tmp/sprint13-controller.html"}},
        {"done": {"success": True, "text": "finished"}},
    ]


def test_controller_replans_after_failed_verification() -> None:
    class RevisingPlanner:
        def __init__(self) -> None:
            self.revisions: list[tuple[str, str]] = []

        async def revise(self, plan: Plan, failed_step: PlanStep, reason: str) -> Plan:
            self.revisions.append((failed_step.sub_goal, reason))
            return Plan(
                task=plan.task,
                steps=[
                    PlanStep(
                        sub_goal="finish with fallback",
                        expected_state="text contains fallback complete",
                        action={"done": {"success": True, "text": "fallback complete"}},
                        max_retries=0,
                    )
                ],
            )

    class FlakyTools:
        def __init__(self) -> None:
            self.actions: list[dict[str, object]] = []

        async def execute_action(self, action: dict[str, object], browser_session: object | None = None) -> dict[str, object]:
            self.actions.append(action)
            if "click" in action:
                return {"ok": True, "text": "wrong state"}
            return {"ok": True, "text": "fallback complete"}

    async def run() -> tuple[Controller, RevisingPlanner, FlakyTools]:
        planner = RevisingPlanner()
        tools = FlakyTools()
        initial = Plan(
            task="recover from failed click",
            steps=[
                PlanStep(
                    sub_goal="click primary button",
                    expected_state="text contains primary complete",
                    action={"click": {"index": 0}},
                    max_retries=1,
                    fallback_strategy="replan",
                )
            ],
        )
        controller = Controller(tools=tools, planner=planner, browser_session=object())
        result = await controller.execute_plan(initial)
        assert result.success is True
        return controller, planner, tools

    controller, planner, tools = asyncio.run(run())

    assert planner.revisions and planner.revisions[0][0] == "click primary button"
    assert len([action for action in tools.actions if "click" in action]) == 2
    assert tools.actions[-1] == {"done": {"success": True, "text": "fallback complete"}}
    states = [entry.state for entry in controller.transition_history]
    assert ControllerState.REPLANNING in states
    assert states[-1] is ControllerState.DONE


def test_controller_operational_api() -> None:
    class SlowTools:
        async def execute_action(self, action: dict[str, object], browser_session: object | None = None) -> dict[str, object]:
            return {"ok": True, "text": "ready"}

    async def run() -> tuple[Controller, StepResult, dict[str, object]]:
        plan = Plan(
            task="inspect controller api",
            steps=[
                PlanStep(
                    sub_goal="prepare pending action",
                    expected_state="text contains ready",
                    action={"done": {"success": True, "text": "ready"}},
                    max_retries=0,
                )
            ],
        )
        controller = Controller(tools=SlowTools(), browser_session=object())
        result = await controller.execute_plan(plan)
        assert result.success is True
        return controller, result.step_results[0], controller.checkpoint()

    controller, first, checkpoint = asyncio.run(run())

    assert controller.step_result(first.pending_action_id).verified is True
    assert checkpoint["state"] == "done"
    assert checkpoint["plan"]["task"] == "inspect controller api"
    assert len(checkpoint["step_results"]) == 1
    controller.abort("manual stop")
    assert controller.state is ControllerState.FAILED
    assert controller.failure_reason == "manual stop"


def test_message_manager_builds_planner_and_controller_contexts() -> None:
    state = BrowserStateSummary(
        url="file:///tmp/sprint13-message.html",
        title="Message Page",
        elements=[{"index": 0, "tag": "button", "text": "Next"}],
    )
    histories = AgentHistoryList(
        histories=[
            AgentHistory(
                model_output=AgentOutput(
                    evaluation=f"eval {index}",
                    memory=f"memory {index}",
                    next_goal=f"goal {index}",
                    actions=[{"click": {"index": index}}],
                ),
                state=state,
            )
            for index in range(5)
        ]
    )
    manager = MessageManager(task="use separated planning", keep_recent_steps=2)
    for history in histories.histories:
        manager.add_history(history)

    planner_text = manager.build_planner_messages(
        task="use separated planning",
        browser_state=state,
        history=histories,
    )[-1]["content"]
    assert "Compressed older history:" in planner_text
    assert "Recent uncompressed history:" in planner_text
    assert "memory 0" in planner_text

    step = StepResult(
        pending_action_id="step-1",
        sub_goal="click next",
        action={"click": {"index": 0}},
        result={"ok": True, "text": "clicked"},
        verified=True,
    )
    controller_text = manager.build_controller_messages(plan_step="click next", step_results=[step])[-1]["content"]
    assert "click next" in controller_text
    assert "step-1" in controller_text
    assert "clicked" in controller_text
    assert "Current browser state:" not in controller_text


def test_agent_runs_separated_planner_controller_path() -> None:
    class UnusedLLM(BaseChatModel):
        def __init__(self) -> None:
            super().__init__(model="unused")

        async def ainvoke(self, messages: list[dict[str, object]], **kwargs: object) -> object:
            raise AssertionError("separated path should not call direct Agent LLM reasoning")

        async def astream(self, messages: list[dict[str, object]], **kwargs: object) -> object:
            if False:
                yield None

    class StaticPlanner:
        async def decompose(self, context: PlanningContext) -> Plan:
            return Plan(
                task=context.task,
                steps=[
                    PlanStep(
                        sub_goal="finish separated run",
                        expected_state="text contains separated done",
                        action={"done": {"success": True, "text": "separated done"}},
                        max_retries=0,
                    )
                ],
            )

    class RecordingTools:
        def __init__(self) -> None:
            self.actions: list[dict[str, object]] = []

        async def execute_action(self, action: dict[str, object], browser_session: object | None = None) -> dict[str, object]:
            self.actions.append(action)
            return {"ok": True, "text": "separated done"}

    class StaticSession:
        async def get_current_url(self) -> str:
            return "file:///tmp/sprint13-agent.html"

        async def get_title(self) -> str:
            return "Separated Agent"

        async def evaluate(self, expression: str, *args: object) -> list[dict[str, object]]:
            return [{"index": 0, "tag": "button", "text": "Done"}]

    async def run() -> tuple[AgentHistoryList, RecordingTools]:
        planner = StaticPlanner()
        tools = RecordingTools()
        controller = Controller(tools=tools, planner=planner, browser_session=StaticSession())
        agent = Agent(
            task="complete through separated loop",
            llm=UnusedLLM(),
            browser_session=StaticSession(),
            tools=tools,
            planner=planner,
            controller=controller,
            max_steps=3,
        )
        return await agent.run(), tools

    history, tools = asyncio.run(run())

    assert len(history.histories) == 1
    entry = history.histories[0]
    assert entry.model_output.next_goal == "finish separated run"
    assert entry.controller_result.success is True
    assert entry.plan.steps[0].sub_goal == "finish separated run"
    assert tools.actions == [{"done": {"success": True, "text": "separated done"}}]
