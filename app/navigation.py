"""Il menu, come dato invece che come markup.

Sta qui e non dentro `base.html` per una ragione precisa: da quando esiste più
di un fornitore, **il menu dipende da cosa l'utente può davvero avere**. Chi usa
Strava non ha sonno né recupero, e la regola scelta è che quelle sezioni per lui
non esistano — non che esistano vuote con una spiegazione.

Una decisione del genere è logica di prodotto: in Python si legge, si prova e si
sbaglia in modo visibile; in un template diventa un `{% if %}` annidato che
nessuno rilegge.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    url: str
    icon: str
    label: str
    # Vero se compare nella barra in basso su telefono; le altre finiscono nel
    # foglio «Altro».
    primary: bool = False
    # La sezione da cui dipende: se non è visibile, la voce non compare.
    # None = c'è sempre, per chiunque.
    requires: str | None = None


@dataclass(frozen=True)
class NavGroup:
    label: str
    items: tuple[NavItem, ...]


ALL_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("Oggi", (
        NavItem("coach", "/coach", "coach", "Coach", primary=True),
        NavItem("chat", "/chat", "chat", "Coach AI", primary=True),
        NavItem("overview", "/", "overview", "Panoramica"),
    )),
    NavGroup("Allenamento", (
        NavItem("activities", "/activities", "activities", "Attività", primary=True),
        NavItem("fitness", "/fitness", "fitness", "Forma", primary=True),
        NavItem("plan", "/plan", "plan", "Piano"),
        NavItem("goals", "/goals", "goal", "Obiettivo"),
    )),
    NavGroup("Salute", (
        NavItem("sleep", "/sleep", "sleep", "Sonno", requires="sleep"),
        NavItem("health", "/health", "health", "Recupero", requires="health"),
        NavItem("body", "/body", "body", "Corpo", requires="body"),
    )),
)

ACCOUNT_ITEMS: tuple[NavItem, ...] = (
    # Prima voce del gruppo account: da dove arrivano i dati è la cosa che si
    # configura per prima e l'unica che, se manca, rende inutile tutto il resto.
    NavItem("connect", "/connect", "sync", "Sorgente dati"),
    NavItem("settings", "/settings", "settings", "Impostazioni"),
    NavItem("devices", "/devices", "devices", "Dispositivi"),
)


def groups_for(sections) -> tuple[NavGroup, ...]:
    """I gruppi ridotti a quello che questo utente può vedere.

    Un gruppo rimasto senza voci sparisce con la sua intestazione: «Salute»
    seguito dal nulla sarebbe peggio che non averlo.
    """
    visible = set(sections or ())
    result = []
    for group in ALL_GROUPS:
        items = tuple(i for i in group.items if i.requires is None or i.requires in visible)
        if items:
            result.append(NavGroup(group.label, items))
    return tuple(result)
