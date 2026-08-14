"""La pagina Coach deve reggere anche a database vuoto."""


def test_coach_page_renders_200(logged_client):
    response = logged_client.get("/coach")
    assert response.status_code == 200
    # Marker sempre presente, indipendente dai dati nel DB.
    assert 'class="coach-hero"' in response.text


def test_coach_page_has_nav_link(logged_client):
    assert 'href="/coach"' in logged_client.get("/coach").text


def test_coach_page_empty_db_still_200(logged_client):
    """Su DB vuoto la pagina non crasha: 200 + invito a sincronizzare."""
    response = logged_client.get("/coach")
    assert response.status_code == 200
    assert 'class="coach-hero"' in response.text
    assert "Sincronizza" in response.text
