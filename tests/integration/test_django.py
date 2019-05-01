from __future__ import absolute_import, division, print_function, unicode_literals

import os.path
from contextlib import contextmanager

import django
import pytest
from django.apps import apps
from django.conf import settings
from django.test.utils import override_settings
from webtest import TestApp

from scout_apm.api import Config
from scout_apm.core.tracked_request import TrackedRequest

from .django_app import app

try:
    from unittest.mock import Mock, patch
except ImportError:  # Python 2
    from mock import Mock, patch


skip_unless_new_style_middleware = pytest.mark.skipif(
    django.VERSION < (1, 10), reason="new-style middleware was added in Django 1.10"
)

skip_unless_old_style_middleware = pytest.mark.skipif(
    django.VERSION >= (2, 0), reason="old-style middleware was removed in Django 2.0"
)


@pytest.fixture(autouse=True)
def ensure_no_django_config_applied_after_tests():
    """
    Prevent state leaking into the non-Django tests. All config needs to be set
    with @override_settings so that the on_setting_changed handler removes
    them from the dictionary afterwards.
    """
    yield
    assert all(
        (key != "BASE_DIR" and not key.startswith("SCOUT_")) for key in dir(settings)
    )


@contextmanager
def app_with_scout(config=None):
    """
    Context manager that simply overrides settings. Unlike the other web
    frameworks, Django uses a singleton application, so we can't smoothly
    uninstall and reinstall scout per test.
    """
    # Enable Scout by default in tests.
    if config is None:
        config = {"SCOUT_MONITOR": True}

    # Disable running the agent.
    config["SCOUT_CORE_AGENT_LAUNCH"] = False

    # Setup according to https://docs.scoutapm.com/#django
    with override_settings(**config):
        yield app


@pytest.fixture(autouse=True)
def finish_tracked_request_if_old_style_middlware():
    # It appears that the current implementation of old-style middleware
    # doesn't always pair start_span() and stop_span() calls. This leaks
    # unfinished TrackedRequest instances across tests.
    # Sweep the dirt under the rug until there's a better solution :-(
    try:
        yield
    finally:
        if django.VERSION < (2, 0):
            TrackedRequest.instance().finish()


def test_on_setting_changed_application_root():
    with override_settings(BASE_DIR="/tmp/foobar"):
        assert Config().value("application_root") == "/tmp/foobar"
    assert Config().value("application_root") == ""


def test_on_setting_changed_monitor():
    with override_settings(SCOUT_MONITOR=True):
        assert Config().value("monitor") is True
    assert Config().value("monitor") is False


def test_home():
    with app_with_scout() as app:
        response = TestApp(app).get("/")
        assert response.status_int == 200


# During the transition between old-style and new-style middleware, Django
# defaults to the old style. Also test with the new style in that case.
@skip_unless_new_style_middleware
@skip_unless_old_style_middleware
def test_home_new_style():
    with override_settings(MIDDLEWARE=[]):
        test_home()


def test_hello():
    with app_with_scout() as app:
        response = TestApp(app).get("/hello/")
        assert response.status_int == 200


# During the transition between old-style and new-style middleware, Django
# defaults to the old style. Also test with the new style in that case.
@skip_unless_new_style_middleware
@skip_unless_old_style_middleware
def test_hello_new_style():
    with override_settings(MIDDLEWARE=[]):
        test_hello()


def test_not_found():
    with app_with_scout() as app:
        response = TestApp(app).get("/not-found/", expect_errors=True)
        assert response.status_int == 404


# During the transition between old-style and new-style middleware, Django
# defaults to the old style. Also test with the new style in that case.
@skip_unless_new_style_middleware
@skip_unless_old_style_middleware
def test_not_found_new_style():
    with override_settings(MIDDLEWARE=[]):
        test_not_found()


def test_server_error():
    with app_with_scout() as app:
        response = TestApp(app).get("/crash/", expect_errors=True)
        assert response.status_int == 500


# During the transition between old-style and new-style middleware, Django
# defaults to the old style. Also test with the new style in that case.
@skip_unless_new_style_middleware
@skip_unless_old_style_middleware
def test_server_error_new_style():
    with override_settings(MIDDLEWARE=[]):
        test_server_error()


def test_sql():
    with app_with_scout() as app:
        response = TestApp(app).get("/sql/")
        assert response.status_int == 200


# Monkey patch should_capture_backtrace in order to keep the test fast.
@patch(
    "scout_apm.core.n_plus_one_call_set.NPlusOneCallSetItem.should_capture_backtrace"
)
def test_sql_capture_backtrace(should_capture_backtrace):
    should_capture_backtrace.return_value = True
    with app_with_scout() as app:
        response = TestApp(app).get("/sql/")
        assert response.status_int == 200


def test_template():
    with app_with_scout() as app:
        response = TestApp(app).get("/template/")
        assert response.status_int == 200


def test_no_monitor():
    # With an empty config, "scout.monitor" defaults to "false".
    with app_with_scout({}) as app:
        response = TestApp(app).get("/hello/")
        assert response.status_int == 200


def fake_authentication_middleware(get_response):
    def middleware(request):
        # Mock the User instance to avoid a dependency on django.contrib.auth.
        request.user = Mock()
        request.user.get_username.return_value = "scout"
        return get_response(request)

    return middleware


@skip_unless_new_style_middleware
def test_username():
    with override_settings(MIDDLEWARE=[__name__ + ".fake_authentication_middleware"]):
        with app_with_scout() as app:
            response = TestApp(app).get("/hello/")
            assert response.status_int == 200


def crashy_authentication_middleware(get_response):
    def middleware(request):
        # Mock the User instance to avoid a dependency on django.contrib.auth.
        request.user = Mock()
        request.user.get_username.side_effect = ValueError
        return get_response(request)

    return middleware


@skip_unless_new_style_middleware
def test_username_exception():
    with override_settings(MIDDLEWARE=[__name__ + ".crashy_authentication_middleware"]):
        with app_with_scout() as app:
            response = TestApp(app).get("/hello/")
            assert response.status_int == 200


class FakeAuthenticationMiddleware(object):
    def process_request(self, request):
        # Mock the User instance to avoid a dependency on django.contrib.auth.
        request.user = Mock()
        request.user.get_username.return_value = "scout"


@skip_unless_old_style_middleware
def test_old_style_username():
    with override_settings(
        MIDDLEWARE_CLASSES=[__name__ + ".FakeAuthenticationMiddleware"]
    ):
        with app_with_scout() as app:
            response = TestApp(app).get("/hello/")
            assert response.status_int == 200


class CrashyAuthenticationMiddleware(object):
    def process_request(self, request):
        # Mock the User instance to avoid a dependency on django.contrib.auth.
        request.user = Mock()
        request.user.get_username.side_effect = ValueError


@skip_unless_old_style_middleware
def test_old_style_username_exception():
    with override_settings(
        MIDDLEWARE_CLASSES=[__name__ + ".CrashyAuthenticationMiddleware"]
    ):
        with app_with_scout() as app:
            response = TestApp(app).get("/hello/")
            assert response.status_int == 200


@pytest.mark.skipif(django.VERSION >= (1, 10), reason="Testing old style middleware")
@pytest.mark.parametrize("list_or_tuple", [list, tuple])
@pytest.mark.parametrize("preinstalled", [True, False])
def test_install_middleware_old_style(list_or_tuple, preinstalled):
    if preinstalled:
        middleware = list_or_tuple(
            [
                "scout_apm.django.middleware.OldStyleMiddlewareTimingMiddleware",
                "django.middleware.common.CommonMiddleware",
                "scout_apm.django.middleware.OldStyleViewMiddleware",
            ]
        )
    else:
        middleware = list_or_tuple(["django.middleware.common.CommonMiddleware"])

    with override_settings(MIDDLEWARE_CLASSES=middleware):
        apps.get_app_config("scout_apm").install_middleware()

        assert settings.MIDDLEWARE_CLASSES == list_or_tuple(
            [
                "scout_apm.django.middleware.OldStyleMiddlewareTimingMiddleware",
                "django.middleware.common.CommonMiddleware",
                "scout_apm.django.middleware.OldStyleViewMiddleware",
            ]
        )


@pytest.mark.skipif(django.VERSION < (1, 10), reason="Testing new style middleware")
@pytest.mark.parametrize("list_or_tuple", [list, tuple])
@pytest.mark.parametrize("preinstalled", [True, False])
def test_install_middleware_new_style(list_or_tuple, preinstalled):
    if preinstalled:
        middleware = list_or_tuple(
            [
                "scout_apm.django.middleware.MiddlewareTimingMiddleware",
                "django.middleware.common.CommonMiddleware",
                "scout_apm.django.middleware.ViewTimingMiddleware",
            ]
        )
    else:
        middleware = list_or_tuple(["django.middleware.common.CommonMiddleware"])

    with override_settings(MIDDLEWARE=middleware):
        apps.get_app_config("scout_apm").install_middleware()

        assert settings.MIDDLEWARE == list_or_tuple(
            [
                "scout_apm.django.middleware.MiddlewareTimingMiddleware",
                "django.middleware.common.CommonMiddleware",
                "scout_apm.django.middleware.ViewTimingMiddleware",
            ]
        )


def test_application_root():
    """
    A BASE_DIR setting is mapped to the application_root config parameter.

    Django doesn't have a BASE_DIR setting. However the default project
    template creates it in order to define other settings. As a consequence,
    most Django projets have it.

    """
    base_dir = os.path.dirname(__file__)
    with override_settings(BASE_DIR=base_dir):
        with app_with_scout():
            assert Config().value("application_root") == base_dir
