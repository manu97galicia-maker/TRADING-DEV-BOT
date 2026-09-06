import unittest

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


if __name__ == "__main__":
    unittest.main()
