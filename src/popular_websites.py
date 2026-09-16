"""Websites most people want a command for, offered by the recommendations scan.

The bookmarks bar covers the sites a person has already kept; this covers the
ones they would have to type out, and gives them the search links that are the
whole point of a keyword launcher: "yt" opens YouTube, "yt live sets" searches
it.

Addresses are the international .com ones on purpose. Google, YouTube, Amazon
and the rest send a visitor to their own country's site, while a country
address here would send everyone else to the wrong one.
"""
from urllib.parse import urlsplit

# (name, address, aliases). Ordered roughly by how many people want them, since
# the scan dialog keeps this order under anything it has usage for.
POPULAR_WEBSITES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Google", "https://www.google.com/search?q={query}", ("g", "search")),
    ("YouTube", "https://www.youtube.com/results?search_query={query}", ("yt",)),
    ("Gmail", "https://mail.google.com/", ("mail",)),
    ("Google Calendar", "https://calendar.google.com/", ("calendar",)),
    ("Google Drive", "https://drive.google.com/", ("drive",)),
    ("Google Maps", "https://maps.google.com/maps?q={query}", ("maps",)),
    ("Google Translate", "https://translate.google.com/?op=translate&text={query}", ("translate",)),
    ("ChatGPT", "https://chatgpt.com/?q={query}", ()),
    ("Claude", "https://claude.ai/new?q={query}", ()),
    ("Wikipedia", "https://en.wikipedia.org/w/index.php?search={query}", ("wiki",)),
    ("GitHub", "https://github.com/search?q={query}", ("gh",)),
    ("Stack Overflow", "https://stackoverflow.com/search?q={query}", ("so",)),
    ("Outlook", "https://outlook.office.com/mail/", ()),
    ("Microsoft Teams", "https://teams.microsoft.com/", ("teams",)),
    # The bare onedrive.live.com lands on Microsoft's product page; this is
    # the address that reaches the files themselves.
    ("OneDrive", "https://onedrive.live.com/?id=root", ()),
    ("LinkedIn", "https://www.linkedin.com/", ()),
    ("Reddit", "https://www.reddit.com/search/?q={query}", ()),
    ("X", "https://x.com/search?q={query}", ("twitter",)),
    ("Facebook", "https://www.facebook.com/", ("fb",)),
    ("Instagram", "https://www.instagram.com/", ("ig",)),
    ("WhatsApp", "https://web.whatsapp.com/", ()),
    ("Amazon", "https://www.amazon.com/s?k={query}", ()),
    ("eBay", "https://www.ebay.com/sch/i.html?_nkw={query}", ()),
    ("Netflix", "https://www.netflix.com/search?q={query}", ()),
    ("Spotify", "https://open.spotify.com/search/{query}", ()),
    ("Twitch", "https://www.twitch.tv/search?term={query}", ()),
    ("IMDb", "https://www.imdb.com/find/?q={query}", ()),
)


def discover_popular_websites(found: list[dict] | None = None) -> list[dict]:
    """Recommendations for the common websites, minus any the scan already
    found for itself: a site kept as a bookmark is offered once, as theirs."""
    taken = {site_host(str(entry.get("location", ""))) for entry in found or []}
    taken.discard("")
    return [
        {
            "name": name,
            "aliases": list(aliases),
            "location": address,
            "description": website_description(address),
            "type": "url",
            "group": "popular",
        }
        for name, address, aliases in POPULAR_WEBSITES
        if site_host(address) not in taken
    ]


def site_host(url: str) -> str:
    """The host of a URL as a person would say it: no scheme, no www."""
    parts = urlsplit(str(url or "").strip())
    if parts.scheme not in ("http", "https"):
        return ""
    return parts.netloc.split("@")[-1].split(":")[0].removeprefix("www.").casefold()


def website_description(address: str) -> str:
    """Worded as the editor words the same site, so a recommendation reads
    like a command the user added themselves."""
    verb = "Searches" if "{query}" in address else "Opens"
    return f"{verb} {site_host(address)}"
