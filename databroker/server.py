import json
import msgpack
from typing import Optional
from jsonschema import ValidationError

from event_model import DocumentNames, schema_validators
from fastapi import APIRouter, HTTPException, Request
import pydantic
from tiled.server.core import PatchedStreamingResponse
from tiled.server.dependencies import SecureEntry


class NameDocumentPair(pydantic.BaseModel):
    name: str  # TODO Lock this down to an enum of the document types.
    document: dict


router = APIRouter()


@router.get("/documents/{path:path}", response_model=NameDocumentPair)
@router.get("/documents", response_model=NameDocumentPair, include_in_schema=False)
def get_documents(
    request: Request,
    fill: Optional[bool] = False,
    run=SecureEntry(scopes=["read:data", "read:metadata"]),
):

    from .mongo_normalized import BlueskyRun

    if not isinstance(run, BlueskyRun):
        raise HTTPException(status_code=404, detail="This is not a BlueskyRun.")
    DEFAULT_MEDIA_TYPE = "application/json"
    media_types = request.headers.get("Accept", DEFAULT_MEDIA_TYPE).split(", ")
    for media_type in media_types:
        if media_type == "*/*":
            media_type = DEFAULT_MEDIA_TYPE
        if media_type == "application/x-msgpack":
            # (name, doc) pairs as msgpack

            def generator_func():
                packer = msgpack.Packer()
                for item in run.documents(fill=fill):
                    yield packer.pack(item)

            generator = generator_func()
            return PatchedStreamingResponse(
                generator, media_type="application/x-msgpack"
            )
        if media_type == "application/json":
            # (name, doc) pairs as newline-delimited JSON
            generator = (json.dumps(item) + "\n" for item in run.documents(fill=fill))
            return PatchedStreamingResponse(
                generator, media_type="application/x-ndjson"
            )
    else:
        raise HTTPException(
            status_code=406,
            detail=", ".join(["application/json", "application/x-msgpack"]),
        )


@router.post("/documents/{path:path}")
@router.post("/documents", include_in_schema=False)
def post_documents(
    request: Request,
    name_doc_pair: NameDocumentPair,
    catalog=SecureEntry(scopes=["write:data", "write:metadata"]),
):
    from .mongo_normalized import MongoAdapter

    # Check that this is a BlueskyRun.
    if not isinstance(catalog, MongoAdapter):
        raise HTTPException(status_code=404, detail="This is not a CatalogOfBlueskyRuns.")
    serializer = catalog.get_serializer()
    try:
        schema_validators[DocumentNames(name_doc_pair.name)].validate(name_doc_pair.document)
    except ValidationError as err:
        raise HTTPException(status_code=400, detail=err.message)
    serializer(name_doc_pair.name, name_doc_pair.document)
