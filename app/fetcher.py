import httpx

_HEADERS = {
    "Content-Type": "application/x-www-form-urlencoded",
    "User-Agent": "TrackTimingPredictor/1.0",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/javascript, */*; q=0.01",
}


async def fetch_page_html(client: httpx.AsyncClient, path: str) -> str:
    """GET a page by relative path and return its HTML content."""
    response = await client.get(path)
    response.raise_for_status()
    return response.text


async def fetch_initial_layout(
    client: httpx.AsyncClient, competition_id: int
) -> dict:
    """POST getInitialPageLayout to get the full schedule HTML."""
    url = f"eventpage.php?EventId={competition_id}"
    headers = {**_HEADERS, "Referer": f"{client.base_url}/{url}"}
    payload = "jxnfun=getInitialPageLayout&jxnr=1"
    response = await client.post(url, content=payload, headers=headers)
    response.raise_for_status()
    return response.json()


async def fetch_refresh(
    client: httpx.AsyncClient, competition_id: int
) -> dict:
    """POST refreshPage to get live status updates."""
    url = f"eventpage.php?EventId={competition_id}"
    headers = {**_HEADERS, "Referer": f"{client.base_url}/{url}"}
    # Pass open session IDs ["1","2"] and group filter "All"
    payload = 'jxnfun=refreshPage&jxnr=1&jxnargs%5B%5D=%5B%221%22%2C%222%22%2C%223%22%5D&jxnargs%5B%5D=All'
    response = await client.post(url, content=payload, headers=headers)
    response.raise_for_status()
    return response.json()
