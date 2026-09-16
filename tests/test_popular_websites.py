import unittest
from urllib.parse import urlsplit

from src.browsers import fill_query, is_search_link
from src.popular_websites import POPULAR_WEBSITES, discover_popular_websites, site_host


class PopularWebsiteTests(unittest.TestCase):
    def test_a_site_is_a_url_command_worded_like_one_the_user_added(self):
        candidates = {candidate["name"]: candidate for candidate in discover_popular_websites()}

        google = candidates["Google"]
        self.assertEqual(google["location"], "https://www.google.com/search?q={query}")
        self.assertEqual(google["type"], "url")
        self.assertEqual(google["description"], "Searches google.com")
        self.assertEqual(google["aliases"], ["g", "search"])

        self.assertEqual(candidates["Gmail"]["description"], "Opens mail.google.com")

    def test_a_site_the_scan_already_found_is_not_offered_again(self):
        found = [{"location": "https://www.youtube.com/feed/subscriptions"}, {"location": r"C:\Tools\app.exe"}]
        names = [candidate["name"] for candidate in discover_popular_websites(found)]
        self.assertNotIn("YouTube", names)
        self.assertIn("Google", names)

    def test_every_address_is_a_plain_https_url(self):
        for name, address, _aliases in POPULAR_WEBSITES:
            with self.subTest(name=name):
                parts = urlsplit(address)
                self.assertEqual(parts.scheme, "https")
                self.assertTrue(parts.netloc)
                self.assertTrue(site_host(address))

    def test_a_search_link_still_opens_the_site_with_nothing_typed(self):
        for name, address, _aliases in POPULAR_WEBSITES:
            if not is_search_link(address):
                continue
            with self.subTest(name=name):
                # What Dash opens for the command's name on its own.
                self.assertEqual(fill_query(address, ""), f"https://{urlsplit(address).netloc}/")
                self.assertIn("dash", fill_query(address, "dash"))

    def test_names_and_aliases_do_not_collide_with_each_other(self):
        keywords = []
        for name, _address, aliases in POPULAR_WEBSITES:
            keywords += [name.casefold(), *(alias.casefold() for alias in aliases)]
        duplicates = {keyword for keyword in keywords if keywords.count(keyword) > 1}
        self.assertEqual(duplicates, set())


if __name__ == "__main__":
    unittest.main()
