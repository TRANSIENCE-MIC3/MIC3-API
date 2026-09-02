from mic3_api.core.config import Settings


def test_database_url_preserves_and_encodes_special_character_password() -> None:
    password = "p@ss/word:with#characters"
    settings = Settings(
        db_host="localhost",
        db_port=5433,
        db_name="mic3",
        db_user="mic3_api",
        db_password=password,
    )

    database_url = settings.database_url
    rendered_url = database_url.render_as_string(hide_password=False)

    assert database_url.drivername == "postgresql+psycopg"
    assert database_url.password == password
    assert password not in rendered_url
    assert "p%40ss%2Fword%3Awith%23characters" in rendered_url
