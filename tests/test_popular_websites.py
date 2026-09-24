import unittest
from urllib.parse import urlsplit

from src.browsers import fill_query, is_search_link
from src.popular_websites import POPULAR_WEBSITES, amazon_domain, discover_popular_websites, site_host, wikipedia_language


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

    def test_wikipedia_and_amazon_follow_the_locale(self):
        sites = {candidate["name"]: candidate["location"] for candidate in discover_popular_websites(locale_name="de_DE")}
        self.assertEqual(sites["Wikipedia"], "https://de.wikipedia.org/w/index.php?search={query}")
        self.assertEqual(sites["Amazon"], "https://www.amazon.de/s?k={query}")
        self.assertEqual(sites["Google"], "https://www.google.com/search?q={query}", "the rest stay international")
        for locale_name, domain in (("en_GB", "amazon.co.uk"), ("ja_JP", "amazon.co.jp"), ("pt-BR", "amazon.com.br"), ("es_MX", "amazon.com.mx"), ("en_AU", "amazon.com.au"), ("en_US", "amazon.com"), ("sv_SE", "amazon.com")):
            with self.subTest(locale_name=locale_name):
                self.assertEqual(amazon_domain(locale_name), domain)

    def test_unknown_locales_fall_back_to_english_and_amazon_com(self):
        for locale_name in ("", None, "C", "English_United Kingdom"):
            with self.subTest(locale_name=locale_name):
                self.assertEqual(wikipedia_language(locale_name), "en")
                self.assertEqual(amazon_domain(locale_name), "amazon.com")
        self.assertEqual(wikipedia_language("fr_CA.UTF-8"), "fr")
        self.assertEqual(wikipedia_language("nb_NO"), "no")



if __name__ == "__main__":
    unittest.main()
