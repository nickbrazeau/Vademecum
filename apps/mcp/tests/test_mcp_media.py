"""Pictures and schematics through the tools (ADR 0013).

A picture comes back as an image content block the host can show the model;
a schematic goes in as SVG text, is checked by the API, and comes back out
as the same SVG.
"""

from __future__ import annotations

import base64

import pytest
from tests.mcp_support import call, call_expecting_error
from tests.test_mcp_tools import seed_bank
from mcp import Client

from test_images_and_schematics import SVG, pdf_with_picture, png

pytestmark = pytest.mark.anyio


async def upload_figure(api, pile_id: str) -> dict:
    uploaded = await api.post(
        f"/api/piles/{pile_id}/sources",
        data={"confidence": "mid"},
        files=[("files", ("figure.pdf", pdf_with_picture("A figure and its caption, in words."), "application/pdf"))],
    )
    return uploaded["results"][0]["source"]


async def test_a_picture_is_listed_and_returned_as_an_image(mcp_client: Client, api) -> None:
    pile = await call(mcp_client, "create_pile", {"title": "Sepsis", "confidence": "mid"})
    source = await upload_figure(api, pile["id"])

    detail = await call(mcp_client, "get_source", {"source_id": source["id"]})
    assert detail["image_count"] == 1

    listed = await call(mcp_client, "list_images", {"source_id": source["id"]})
    assert listed["count"] == 1
    image = listed["items"][0]
    assert image["locator"] == "page 1" and image["origin"] == "embedded"
    assert "stored_name" not in image

    result = await mcp_client.call_tool("view_image", {"image_id": image["id"]})
    assert not result.is_error
    block = result.content[0]
    assert block.type == "image" and block.mime_type == "image/png"
    assert base64.b64decode(block.data)[:8] == b"\x89PNG\r\n\x1a\n"


async def test_an_unknown_picture_is_a_plain_refusal(mcp_client: Client) -> None:
    message = await call_expecting_error(mcp_client, "view_image", {"image_id": "img_nope"})
    assert "No such image" in message


async def test_a_schematic_round_trips_through_the_tools(mcp_client: Client) -> None:
    await seed_bank(mcp_client)
    point = (await call(mcp_client, "list_learning_points", {}))["items"][0]

    saved = await call(
        mcp_client, "save_schematic", {"point_id": point["id"], "title": "Lactate pathway", "svg": SVG}
    )
    assert saved["learning_point_id"] == point["id"]
    assert saved["support_label"] == point["support_label"]
    assert "stored_name" not in saved and "saved_to_folder" in saved

    listed = await call(mcp_client, "list_schematics", {"point_id": point["id"]})
    assert [item["id"] for item in listed["items"]] == [saved["id"]]

    fetched = await call(mcp_client, "get_schematic", {"schematic_id": saved["id"]})
    assert fetched["media_type"] == "image/svg+xml"
    assert "<rect" in fetched["svg"] and "Lactate" in fetched["svg"]


async def test_an_unsafe_schematic_is_refused_with_the_reason_only(mcp_client: Client) -> None:
    await seed_bank(mcp_client)
    point = (await call(mcp_client, "list_learning_points", {}))["items"][0]
    message = await call_expecting_error(
        mcp_client,
        "save_schematic",
        {
            "point_id": point["id"],
            "title": "Bad",
            "svg": '<svg xmlns="http://www.w3.org/2000/svg"><script>alert("secret-token")</script></svg>',
        },
    )
    assert "secret-token" not in message
    assert "script" in message.lower()
    listed = await call(mcp_client, "list_schematics", {"point_id": point["id"]})
    assert listed["count"] == 0
