from scripts.generate_openapi_types import TYPES_PATH, build_openapi_spec, render_types


def test_openapi_type_generation_is_up_to_date():
    spec = build_openapi_spec()

    assert TYPES_PATH.read_text(encoding="utf-8") == render_types(spec)


def test_openapi_type_generation_contains_frontend_contracts():
    generated = render_types(build_openapi_spec())

    assert "export type StrategySummaryResponse" in generated
    assert "export type LogicalPositionUnitResponse" in generated
    assert '"current_intent"?: PositionIntentResponse | null;' in generated
    assert '"loc": (string | number)[];' in generated
