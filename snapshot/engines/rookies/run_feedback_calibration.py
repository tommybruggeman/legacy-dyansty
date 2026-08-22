from auth import service_client
from snapshot.engines.rookies.rookie_feature_engine import RookieFeatureEngine


def run_calibration():
    sb = service_client()

    feedback = (
        sb.table("rookie_model_feedback")
        .select("*")
        .execute()
        .data
        or []
    )

    engine = RookieFeatureEngine()
    weights = engine.calibrator.update_weights_from_feedback(feedback)

    print("\n✅ UPDATED MODEL WEIGHTS:")
    for k, v in weights.items():
        print(k, "→", round(v, 3))


if __name__ == "__main__":
    run_calibration()
