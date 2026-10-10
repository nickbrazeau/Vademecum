"""Case series feeds the owner adds (feedback of 10 October): the host is named and
confirmed before anything is contacted, and only that host is ever contacted for it."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import LOCAL_ORIGIN, refusing_factory
from vademecum.app import create_app
from vademecum.config import Settings
from vademecum.literature import cases as feeds
from vademecum.literature.http import HttpsFetcher, ProviderError
from vademecum.storage import cases as store

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Core IM</title>
<item><title>Hyponatraemia, a case</title><link>https://example.org/ep1</link><guid>ep-1</guid>
<pubDate>Wed, 08 Oct 2026 10:00:00 GMT</pubDate><content:encoded><![CDATA[<p>A 70-year-old with confusion.</p>]]></content:encoded></item>
<item><title>No link</title></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Morning report</title>
<entry><title>Fever and rash</title><link href="https://example.org/a"/><id>urn:a</id><updated>2026-10-07T00:00:00Z</updated><summary>Notes.</summary></entry>
</feed>"""


def test_rss_and_atom_feeds_are_read_to_titles_links_dates_and_text() -> None:
    title, items = feeds.parse_feed(RSS, "feed_x")
    assert title == "Core IM" and len(items) == 1
    assert items[0].url == "https://example.org/ep1" and items[0].published_on == "2026-10-08"
    assert items[0].text == "A 70-year-old with confusion." and items[0].credit == "Core IM"
    title, items = feeds.parse_feed(ATOM, "feed_y")
    assert title == "Morning report" and items[0].title == "Fever and rash" and items[0].published_on == "2026-10-07"


def test_a_feed_address_must_be_https_and_name_a_site() -> None:
    assert feeds.feed_parts("https://Example.org/feed.xml?x=1") == ("example.org", "/feed.xml", {"x": "1"})
    for bad in ("http://example.org/feed", "https://user:pw@example.org/", "ftp://example.org", "https://example.org:8443/"):
        with pytest.raises(ValueError):
            feeds.feed_parts(bad)


def test_the_client_refuses_a_host_nobody_confirmed() -> None:
    with pytest.raises(ProviderError):
        HttpsFetcher("example.org", timeout=5)
    assert HttpsFetcher("example.org", timeout=5, owner_confirmed=frozenset({"example.org"}))


def test_a_feed_is_proposed_without_contact_then_added_on_confirmation(tmp_path: Path, monkeypatch) -> None:
    contacted: list[str] = []

    def fake_fetch(fetcher, url, series):
        contacted.append(url)
        return feeds.parse_feed(RSS, series)

    monkeypatch.setattr(feeds, "fetch_feed", fake_fetch)
    settings = Settings(data_dir=tmp_path / "data", host="127.0.0.1", port=8765, sources_folder_enabled=False, sync_role="domi")
    with TestClient(create_app(settings, transport_factory=refusing_factory()), base_url=LOCAL_ORIGIN) as client:
        asked = client.post("/api/cases/feeds", json={"url": "https://example.org/feed.xml"}).json()
        assert asked["host"] == "example.org" and "contact example.org" in asked["ask"] and contacted == []
        added = client.post("/api/cases/feeds", json={"url": "https://example.org/feed.xml", "confirm": True})
        if added.status_code == 409 and added.json()["error"]["code"] == "on_the_mac":
            pytest.skip("this test node does not fetch case series")
        body = added.json()
        assert contacted == ["https://example.org/feed.xml"] and body["new"] == 1
        settings_now = client.get("/api/cases/settings").json()
        assert settings_now["feeds"][0]["host"] == "example.org" and settings_now["series"][body["feed"]["id"]] is True
        assert client.delete(f"/api/cases/feeds/{body['feed']['id']}").json()["removed"] is True


def test_feeds_survive_saving_the_other_settings(connection) -> None:
    feed = store.add_feed(connection, url="https://example.org/f", host="example.org", title="Core IM")
    store.set_settings(connection, enabled=True, interval_hours=6, series={feed["id"]: False})
    assert store.get_settings(connection)["series"][feed["id"]] is False
    assert [f["id"] for f in store.feeds(connection)] == [feed["id"]]
