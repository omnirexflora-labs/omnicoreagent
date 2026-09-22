from omnicoreagent.core.agents.loop_detection import NativeLoopDetector, ToolInteraction


def interaction(name="lookup", *, server=None, arguments=None, result="same"):
    return ToolInteraction(
        "mcp" if server else "local", server, name, arguments or {}, result
    )


def test_duplicate_calls_in_one_batch_are_one_round():
    detector = NativeLoopDetector()
    detector.record_round([interaction()] * 20)
    assert not detector.is_looping()


def test_server_identity_prevents_same_name_calls_collapsing():
    detector = NativeLoopDetector()
    for server in range(5):
        detector.record_round([interaction(server=str(server))])
    assert not detector.is_looping()


def test_reordered_batches_repeat_but_changing_sibling_makes_progress():
    detector = NativeLoopDetector()
    for i in range(5):
        calls = [interaction("a"), interaction("b")]
        detector.record_round(calls if i % 2 else calls[::-1])
    assert detector.is_looping()
    detector.record_round([interaction("a"), interaction("b", result="new")])
    assert not detector.is_looping()


def test_multi_round_cycle_and_reset():
    detector = NativeLoopDetector()
    for _ in range(4):
        detector.record_round([interaction("a")])
        detector.record_round([interaction("b")])
    assert not detector.is_looping()
    detector.record_round([interaction("a")])
    detector.record_round([interaction("b")])
    assert detector.is_looping()
    detector.reset()
    assert not detector.is_looping()


def test_arguments_and_result_changes_are_progress():
    detector = NativeLoopDetector()
    for i in range(10):
        detector.record_round([interaction(arguments={"n": i})])
    assert not detector.is_looping()
    for i in range(10):
        detector.record_round([interaction(result=i)])
    assert not detector.is_looping()
