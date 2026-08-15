from django.test import SimpleTestCase, override_settings

from accounts.url_utils import build_backend_url, build_frontend_url


class DeploymentUrlTests(SimpleTestCase):
    @override_settings(BACKEND_URL="https://api.seedvest.example/")
    def test_backend_url_uses_the_configured_public_host(self):
        self.assertEqual(
            build_backend_url("/api/accounts/activate/uid/token/"),
            "https://api.seedvest.example/api/accounts/activate/uid/token/",
        )

    @override_settings(FRONTEND_URL="seedvest://")
    def test_mobile_url_preserves_the_deep_link_host(self):
        self.assertEqual(
            build_frontend_url("/reset-password/uid/token/"),
            "seedvest://reset-password/uid/token/",
        )

    @override_settings(FRONTEND_URL="https://app.seedvest.example/")
    def test_web_frontend_url_has_one_separator(self):
        self.assertEqual(
            build_frontend_url("/reset-password/uid/token/"),
            "https://app.seedvest.example/reset-password/uid/token/",
        )
