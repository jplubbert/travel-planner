import os

import uvicorn

if __name__ == "__main__":
    # Single worker on purpose: idempotency and booking writes are serialized in-process.
    uvicorn.run(
        "provider_mock.app:create_app",
        factory=True,
        host=os.environ.get("MOCK_HOST", "0.0.0.0"),
        port=int(os.environ.get("MOCK_PORT", "4010")),
        workers=1,
    )
