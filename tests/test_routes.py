import unittest
from pathlib import Path

from server.app import create_app


class RouteTests(unittest.TestCase):
    def test_frontend_multiwallet_routes_exist(self) -> None:
        app = create_app()
        routes = {(route.method, route.resource.canonical) for route in app.router.routes()}
        expected = {
            ("GET", "/api/mw/list"),
            ("POST", "/api/mw/create"),
            ("POST", "/api/mw/spread"),
            ("POST", "/api/mw/collect"),
            ("POST", "/api/mw/buy"),
            ("POST", "/api/mw/sell"),
        }
        self.assertTrue(expected.issubset(routes))

    def test_mayhem_strategy_card_is_paper_only(self) -> None:
        html = Path("server/static/index.html").read_text(encoding="utf-8")
        self.assertIn('id="mayhemTitle"', html)
        self.assertIn("0,03 SOL", html)
        self.assertIn("Solo PAPER", html)
        self.assertIn('id="prepareMayhem03"', html)
        self.assertIn("Casi sin riesgo", html)


if __name__ == "__main__":
    unittest.main()
