import httpx

from app.config import settings

_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "TrackTimingPredictor/1.0",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


def _event_url(competition_id: int) -> str:
    return f"{settings.tracktiming_base_url}/eventpage.php?EventId={competition_id}"


async def fetch_initial_layout(competition_id: int) -> dict:
    """POST getInitialPageLayout to get the full schedule HTML."""
    url = _event_url(competition_id)
    headers = {**_HEADERS, "Referer": url}
    payload = "jxnfun=getInitialPageLayout&jxnr=1"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, content=payload, headers=headers)
        response.raise_for_status()
        return response.json()


async def fetch_result_html(result_path: str) -> str:
    """GET a static result .htm file and return its HTML content."""
    url = f"{settings.tracktiming_base_url}/{result_path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_start_list_html(start_list_path: str) -> str:
    """GET a static start list .htm file and return its HTML content."""
    url = f"{settings.tracktiming_base_url}/{start_list_path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_live_html(live_path: str) -> str:
    """GET a live results page and return its HTML content."""
    url = f"{settings.tracktiming_base_url}/{live_path}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text


async def fetch_refresh(competition_id: int) -> dict:
    """POST refreshPage to get live status updates."""
    url = _event_url(competition_id)
    headers = {**_HEADERS, "Referer": url}
    # Pass open session IDs ["1","2"] and group filter "All"
    payload = 'jxnfun=refreshPage&jxnr=1&jxnargs%5B%5D=%5B%221%22%2C%222%22%2C%223%22%5D&jxnargs%5B%5D=All'
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, content=payload, headers=headers)
        response.raise_for_status()
        return response.json()
