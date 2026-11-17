import pytest


class TendersAPI:
    def __init__(self, accounts):
        self.accounts = accounts
        self.client = accounts.client
        self.headers = accounts.headers(accounts.manager_pair)
        response = self.client.post(
            "/api/v1/buyers",
            headers=self.headers,
            json={"name": "Synthetic buyer", "customer_number": "000123"},
        )
        assert response.status_code == 201, response.text
        self.buyer = response.json()
        self.buyer_etag = response.headers["ETag"]

    def create_response(self, *, key=None, **changes):
        return self.client.post(
            "/api/v1/tenders",
            headers={**self.headers, **({"Idempotency-Key": key} if key else {})},
            json={
                "buyer_id": self.buyer["id"],
                "title": "Synthetic request",
                **changes,
            },
        )

    def create(self, **changes):
        response = self.create_response(**changes)
        assert response.status_code == 201, response.text
        return response.json()

    def get(self, tender_id):
        return self.client.get(f"/api/v1/tenders/{tender_id}", headers=self.headers)

    def patch(self, tender_id, *, etag=None, headers=None, **changes):
        return self.client.patch(
            f"/api/v1/tenders/{tender_id}",
            headers={
                **(self.headers if headers is None else headers),
                **({"If-Match": etag} if etag else {}),
            },
            json=changes,
        )


@pytest.fixture
def tenders_api(local_accounts):
    return TendersAPI(local_accounts)
