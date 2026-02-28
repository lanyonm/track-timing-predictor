import httpx

from app.config import settings

_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "TrackTimingPredictor/1.0",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def _event_url(event_id: int) -> str:
    return f"{settings.tracktiming_base_url}/eventpage.php?EventId={event_id}"


async def fetch_initial_layout(event_id: int) -> dict:
    """POST getInitialPageLayout to get the full schedule HTML."""
    url = _event_url(event_id)
    headers = {**_HEADERS, "Referer": url}
    payload = "jxnfun=getInitialPageLayout&jxnr=1"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, content=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def fetch_refresh(event_id: int) -> dict:
    """POST refreshPage to get live status updates."""
    url = _event_url(event_id)
    headers = {**_HEADERS, "Referer": url}
    # Pass open session IDs ["1","2"] and group filter "All"
    payload = 'jxnfun=refreshPage&jxnr=1&jxnargs%5B%5D=%5B%221%22%2C%222%22%2C%223%22%5D&jxnargs%5B%5D=All'
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, content=payload, headers=headers)
        response.raise_for_status()
        return response.json()
