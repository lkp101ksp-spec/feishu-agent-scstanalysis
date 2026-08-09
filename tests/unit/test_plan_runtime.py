from orchestrator.planner.dag_schema import DAGNode
from orchestrator.runtime.plan_runtime import PlanRuntime, RuntimeState
from shared.errors import DAGValidationError, DynamicAppendError, LoopMaxIterError


class FakeStateRepo:
    def __init__(self):
        self.states = {}

    def upsert(self, *, plan_id, session_id=None, state_json=None, status="running"):
        self.states[plan_id] = {
            "session_id": session_id,
            "state_json": state_json,
            "status": status,
        }

    def get_or_none(self, plan_id):
        return self.states.get(plan_id)


class FakeAuditRepo:
    def __init__(self):
        self.logs = []

    def write(self, **kw):
        self.logs.append(kw)


def test_runtime_state_enum():
    assert RuntimeState.RUNNING.value == "running"
    assert RuntimeState.TERMINAL.value == "terminal"


def test_runtime_initializes_state():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    assert rt.state == RuntimeState.INIT


def test_append_dynamic_nodes_validates_dag():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    sub = DAGNode(node_id="b1a", kind="tool", tool_name="a",
                  inputs={}, depends_on=["b1"])
    rt.append_dynamic_nodes(plan_id="p", parent_node_id="b1", new_nodes=[sub])
    state = rt.state_repo.get_or_none("p")
    assert state["state_json"]["dynamic_nodes"][0]["node_ids"] == ["b1a"]


def test_append_dynamic_nodes_cycle_fails():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    sub1 = DAGNode(node_id="sub1", kind="tool", tool_name="a",
                   inputs={}, depends_on=["sub2"])
    sub2 = DAGNode(node_id="sub2", kind="tool", tool_name="b",
                   inputs={}, depends_on=["sub1"])
    try:
        rt.append_dynamic_nodes(plan_id="p", parent_node_id="p1",
                                 new_nodes=[sub1, sub2])
        assert False
    except (DynamicAppendError, DAGValidationError):
        pass


def test_loop_iteration_done_increments():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo(),
                     max_iterations=3)
    n1 = rt.loop_iteration_done("loop_1")
    n2 = rt.loop_iteration_done("loop_1")
    n3 = rt.loop_iteration_done("loop_1")
    assert n1 == 1 and n2 == 2 and n3 == 3


def test_loop_iteration_done_exceeds_max():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo(),
                     max_iterations=2)
    rt.loop_iteration_done("loop_1")
    rt.loop_iteration_done("loop_1")
    try:
        rt.loop_iteration_done("loop_1")
        assert False
    except LoopMaxIterError:
        pass


def test_freeze_session_returns_new_id():
    rt = PlanRuntime(state_repo=FakeStateRepo(), audit_repo=FakeAuditRepo())
    new_id = rt.freeze_session(origin_session_id="s1", summary="x")
    assert new_id != "s1"