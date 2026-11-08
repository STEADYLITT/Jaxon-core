"""ASGI entrypoint: uvicorn jaxon.main:app"""

from jaxon.api.app import create_app

app = create_app()
