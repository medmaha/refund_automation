import pytest

from src.tests.uat.uat_fixtures import get_mock_success_refund_response


@pytest.fixture
def mock_post_side_effect(*args, **kwargs):
    return get_mock_success_refund_response(
        amount="60.0",
        refund_id="gid://shopify/Refund/BERR2_PARTIAL_SUCCESS",
    )
