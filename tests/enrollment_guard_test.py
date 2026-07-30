import unittest

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.security import EnrollmentRequestGuardMiddleware


def guarded_app(maximum_body_bytes=32, attempts_per_minute=3):
    app = FastAPI()
    app.add_middleware(
        EnrollmentRequestGuardMiddleware,
        maximum_body_bytes=maximum_body_bytes,
        attempts_per_minute=attempts_per_minute,
    )

    @app.post("/api/v1/device-enrollment/challenges")
    async def enrollment(request: Request):
        return {"received": len(await request.body())}

    @app.post("/api/chat")
    async def chat(request: Request):
        return {"received": len(await request.body())}

    return app


class EnrollmentRequestGuardTests(unittest.TestCase):
    def test_rejects_declared_and_streamed_oversized_enrollment_bodies(self):
        with TestClient(guarded_app()) as client:
            declared = client.post(
                "/api/v1/device-enrollment/challenges",
                content=b"x" * 33,
            )
        self.assertEqual(declared.status_code, 413)
        self.assertEqual(declared.json()["error"]["code"], "device_enrollment_body_too_large")

        with TestClient(guarded_app()) as client:
            streamed = client.post(
                "/api/v1/device-enrollment/challenges",
                content=(chunk for chunk in (b"x" * 20, b"y" * 20)),
            )
        self.assertEqual(streamed.status_code, 413)

    def test_rate_limits_by_client_before_endpoint_work(self):
        with TestClient(guarded_app(attempts_per_minute=2)) as client:
            responses = [
                client.post("/api/v1/device-enrollment/challenges", content=b"{}")
                for _ in range(3)
            ]
        self.assertEqual([response.status_code for response in responses], [200, 200, 429])
        self.assertEqual(responses[-1].headers["retry-after"], "60")

    def test_non_enrollment_routes_are_not_capped_or_rate_limited(self):
        with TestClient(guarded_app(maximum_body_bytes=8, attempts_per_minute=1)) as client:
            responses = [client.post("/api/chat", content=b"x" * 40) for _ in range(2)]
        self.assertEqual([response.status_code for response in responses], [200, 200])


if __name__ == "__main__":
    unittest.main()
