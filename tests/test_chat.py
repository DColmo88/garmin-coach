"""La chat col coach: conversazioni, streaming, quote, isolamento."""
from __future__ import annotations

from datetime import date

import pytest

from app.ai.base import AIProvider, Finished, TextChunk, ToolStarted
from app.db.models import AIUsageLog, ChatConversation, ChatMessage, User


class ScriptedProvider(AIProvider):
    """Provider finto: recita eventi prestabiliti ed esegue davvero i tool."""

    def __init__(self, events=None, call_tool: str | None = None):
        self.events = events or [TextChunk("Ciao! "), TextChunk("Come va?")]
        self.call_tool = call_tool
        self.received_system: str | None = None
        self.received_messages: list[dict] | None = None
        self.tool_output: str | None = None

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def supports_chat(self) -> bool:
        return True

    def generate_training_plan(self, context, goal):
        return "piano"

    def chat(self, system, messages, tools, execute_tool, max_rounds=6):
        self.received_system = system
        self.received_messages = list(messages)
        tools_used = []
        if self.call_tool:
            yield ToolStarted(self.call_tool, {})
            self.tool_output = execute_tool(self.call_tool, {})
            tools_used.append(self.call_tool)
        yield from self.events
        yield Finished(tokens_in=420, tokens_out=95, model="scripted-light",
                       tools_used=tools_used)


class NoChatProvider(AIProvider):
    @property
    def name(self) -> str:
        return "stub"

    def generate_training_plan(self, context, goal):
        return ""


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", display_name="Davide",
             password_hash="h")
    db.add(u)
    db.commit()
    return u


def use_provider(monkeypatch, provider):
    """Sostituisce il provider ovunque venga risolto."""
    monkeypatch.setattr("app.ai.chat.get_provider", lambda: provider)
    monkeypatch.setattr("app.routers.chat.get_provider", lambda: provider)


def drain(generator) -> list:
    return list(generator)


# ============================================================================
# Conversazioni
# ============================================================================

def test_create_and_list(db, user):
    from app.ai import chat

    assert chat.list_conversations(db, user.id) == []
    conversation = chat.create_conversation(db, user.id)
    assert conversation.title == "Nuova conversazione"
    assert [c.id for c in chat.list_conversations(db, user.id)] == [conversation.id]


def test_conversations_are_isolated(db, user):
    from app.ai import chat

    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()

    mine = chat.create_conversation(db, user.id)
    theirs = chat.create_conversation(db, other.id)

    assert chat.get_conversation(db, user.id, theirs.id) is None
    assert chat.get_conversation(db, user.id, mine.id) is not None
    assert [c.id for c in chat.list_conversations(db, other.id)] == [theirs.id]


def test_delete_conversation_removes_messages(db, user):
    from app.ai import chat

    conversation = chat.create_conversation(db, user.id)
    db.add(ChatMessage(conversation_id=conversation.id, role="user", content="ciao"))
    db.commit()

    assert chat.delete_conversation(db, user.id, conversation.id) is True
    assert db.query(ChatConversation).count() == 0
    assert db.query(ChatMessage).count() == 0


def test_cannot_delete_someone_elses_conversation(db, user):
    from app.ai import chat

    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()
    theirs = chat.create_conversation(db, other.id)

    assert chat.delete_conversation(db, user.id, theirs.id) is False
    assert db.query(ChatConversation).count() == 1


# ============================================================================
# Invio dei messaggi
# ============================================================================

def test_send_message_persists_both_sides(db, user, monkeypatch):
    from app.ai import chat

    use_provider(monkeypatch, ScriptedProvider())
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "Come sto andando?"))

    messages = chat.messages_of(db, conversation.id)
    assert [m.role for m in messages] == ["user", "assistant"]
    assert messages[0].content == "Come sto andando?"
    assert messages[1].content == "Ciao! Come va?"


def test_title_comes_from_the_first_message(db, user, monkeypatch):
    from app.ai import chat

    use_provider(monkeypatch, ScriptedProvider())
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "Il mio sonno sta peggiorando?"))
    assert conversation.title == "Il mio sonno sta peggiorando?"

    drain(chat.send_message(db, user, conversation, "E il carico?"))
    assert conversation.title == "Il mio sonno sta peggiorando?"  # non cambia più


def test_long_title_is_truncated(db, user, monkeypatch):
    from app.ai import chat

    use_provider(monkeypatch, ScriptedProvider())
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "parola " * 40))
    assert len(conversation.title) <= chat.TITLE_MAX_CHARS
    assert conversation.title.endswith("…")


def test_usage_is_recorded(db, user, monkeypatch):
    from app.ai import chat

    use_provider(monkeypatch, ScriptedProvider())
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "ciao"))

    log = db.query(AIUsageLog).one()
    assert log.kind == "chat" and log.tokens_in == 420 and log.model == "scripted-light"


def test_quota_blocks_the_send(db, user, monkeypatch):
    from app.ai import chat, usage

    use_provider(monkeypatch, ScriptedProvider())
    user.ai_quota_chat_daily = 1
    db.commit()

    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "primo"))

    with pytest.raises(usage.QuotaExceeded):
        drain(chat.send_message(db, user, conversation, "secondo"))

    # il messaggio rifiutato non viene salvato
    assert [m.content for m in chat.messages_of(db, conversation.id) if m.role == "user"] == [
        "primo"
    ]


def test_provider_without_chat_is_refused(db, user, monkeypatch):
    from app.ai import chat

    use_provider(monkeypatch, NoChatProvider())
    conversation = chat.create_conversation(db, user.id)

    with pytest.raises(chat.ChatUnavailable):
        drain(chat.send_message(db, user, conversation, "ciao"))


def test_tools_used_are_recorded_on_the_message(db, user, monkeypatch):
    from app.ai import chat

    provider = ScriptedProvider(call_tool="get_sleep")
    use_provider(monkeypatch, provider)
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "come dormo?"))

    assistant = chat.messages_of(db, conversation.id)[-1]
    assert assistant.tools_used_json == ["get_sleep"]


def test_tool_reads_real_user_data(db, user, monkeypatch):
    """Il tool eseguito dalla chat legge davvero il database dell'utente."""
    from app.ai import chat
    from app.db.models import SleepRecord

    db.add(SleepRecord(user_id=user.id, day=date.today(), total_sleep_sec=7 * 3600,
                       deep_sleep_sec=5400, sleep_score=88))
    db.commit()

    provider = ScriptedProvider(call_tool="get_sleep")
    use_provider(monkeypatch, provider)
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "come dormo?"))

    assert "score 88" in provider.tool_output


# ============================================================================
# Contesto inviato al modello
# ============================================================================

def test_system_prompt_carries_name_and_state(db, user, monkeypatch):
    from app.ai import chat

    provider = ScriptedProvider()
    use_provider(monkeypatch, provider)
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "ciao"))

    assert "Davide" in provider.received_system
    assert "PROFILO" in provider.received_system
    assert "PRONTEZZA DI OGGI" in provider.received_system


def test_the_system_prompt_carries_the_series_not_just_today(db, user, monkeypatch):
    """Il modello deve vedere l'andamento, non l'ultimo campione.

    Senza le serie reagiva a una notte storta come a una crisi: è il motivo per
    cui il briefing ha sostituito lo snapshot puntuale.
    """
    from datetime import date, timedelta

    from app.ai import chat
    from app.db.models import DailyWellness, SleepRecord

    today = date.today()
    # Dodici notti buone, poi una storta: il caso che prima portava fuori strada.
    for i in range(13):
        db.add(SleepRecord(user_id=user.id, day=today - timedelta(days=i),
                           sleep_score=41 if i == 0 else 82, total_sleep_sec=7 * 3600))
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i),
                             resting_hr=56, body_battery_high=88))
    db.commit()

    provider = ScriptedProvider()
    use_provider(monkeypatch, provider)
    conversation = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, conversation, "come sto?"))

    system = provider.received_system
    assert "ANDAMENTO 14 GIORNI" in system
    assert "fuori norma da 1 giorno" in system, "manca il segnale che distingue il giorno dal trend"
    assert "Una giornata storta non è un trend" in system


def test_history_is_capped(db, user, monkeypatch):
    """Una conversazione lunga non manda tutta la cronologia al modello."""
    from app.ai import chat

    provider = ScriptedProvider()
    use_provider(monkeypatch, provider)
    conversation = chat.create_conversation(db, user.id)

    for i in range(20):
        db.add(ChatMessage(conversation_id=conversation.id, role="user", content=f"m{i}"))
        db.add(ChatMessage(conversation_id=conversation.id, role="assistant", content=f"r{i}"))
    db.commit()

    drain(chat.send_message(db, user, conversation, "ultimo"))
    assert len(provider.received_messages) == chat.HISTORY_LIMIT
    assert provider.received_messages[-1]["content"] == "ultimo"


def test_history_excludes_other_conversations(db, user, monkeypatch):
    from app.ai import chat

    provider = ScriptedProvider()
    use_provider(monkeypatch, provider)
    first = chat.create_conversation(db, user.id)
    db.add(ChatMessage(conversation_id=first.id, role="user", content="segreto"))
    db.commit()

    second = chat.create_conversation(db, user.id)
    drain(chat.send_message(db, user, second, "ciao"))

    contents = [m["content"] for m in provider.received_messages]
    assert "segreto" not in contents


# ============================================================================
# Pagina e streaming HTTP
# ============================================================================

def test_chat_page_requires_login(client):
    assert client.get("/chat").status_code == 303


def test_chat_page_shows_welcome_and_suggestions(logged_client):
    page = logged_client.get("/chat").text
    assert "Il coach conosce i tuoi dati" in page
    assert "Come sto andando rispetto al mese scorso?" in page


def test_chat_page_warns_when_provider_missing(logged_client, monkeypatch):
    monkeypatch.setattr("app.routers.chat.get_provider", lambda: NoChatProvider())
    page = logged_client.get("/chat").text
    assert "La chat non è attiva" in page
    assert "disabled" in page


def test_new_conversation_redirects(logged_client):
    response = logged_client.post("/chat/new")
    assert response.status_code == 303
    assert "/chat?conversation=" in response.headers["location"]


def test_send_streams_sse_events(logged_client, monkeypatch):
    use_provider(monkeypatch, ScriptedProvider())
    response = logged_client.post("/chat/send", data={"message": "ciao"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    body = response.text
    assert "event: start" in body
    assert "event: chunk" in body
    assert "event: done" in body
    assert "Come va?" in body


def test_send_reports_tool_usage_in_the_stream(logged_client, monkeypatch):
    use_provider(monkeypatch, ScriptedProvider(call_tool="get_activities"))
    body = logged_client.post("/chat/send", data={"message": "che ho fatto?"}).text
    assert "event: tool" in body
    assert "i tuoi allenamenti" in body


def test_send_rejects_empty_message(logged_client, monkeypatch):
    use_provider(monkeypatch, ScriptedProvider())
    body = logged_client.post("/chat/send", data={"message": "   "}).text
    assert "event: error" in body and "vuoto" in body


def test_send_reports_quota_error_in_the_stream(logged_client, test_db, monkeypatch):
    from sqlalchemy import select

    use_provider(monkeypatch, ScriptedProvider())

    session = test_db()
    who = session.scalar(select(User))
    who.ai_quota_chat_daily = 0
    session.commit()
    session.close()

    body = logged_client.post("/chat/send", data={"message": "ciao"}).text
    assert "event: error" in body and "esaurito" in body


def test_send_reports_provider_failure_gracefully(logged_client, monkeypatch):
    from app.ai.base import AIProviderError

    class BrokenProvider(ScriptedProvider):
        def chat(self, system, messages, tools, execute_tool, max_rounds=6):
            raise AIProviderError("529 overloaded")
            yield  # pragma: no cover

    use_provider(monkeypatch, BrokenProvider())
    body = logged_client.post("/chat/send", data={"message": "ciao"}).text
    assert "event: error" in body and "non ha risposto" in body


def test_conversation_appears_in_the_sidebar_after_sending(logged_client, monkeypatch):
    use_provider(monkeypatch, ScriptedProvider())
    logged_client.post("/chat/send", data={"message": "Quanto ho corso a luglio?"})

    page = logged_client.get("/chat").text
    assert "Quanto ho corso a luglio?" in page
    assert "Come va?" in page  # la risposta è persistita


def test_cannot_open_another_users_conversation(logged_client, test_db):
    from app.ai import chat

    session = test_db()
    intruder = User(email="altro@x.it", password_hash="h")
    session.add(intruder)
    session.commit()
    theirs = chat.create_conversation(session, intruder.id)
    session.add(ChatMessage(conversation_id=theirs.id, role="user", content="dati riservati"))
    session.commit()
    conversation_id = theirs.id
    session.close()

    page = logged_client.get(f"/chat?conversation={conversation_id}").text
    assert "dati riservati" not in page
