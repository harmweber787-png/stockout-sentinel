"""Tests des LINDAS-Erhebungsskripts.

Getestet wird, was ohne Netz pruefbar ist: Aufbau der Abfragen, Auswertung
einer Antwort, Rendering - und dass der Trockenlauf wirklich nichts sendet.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.probe_lindas import (
    Binding,
    FieldRow,
    ProbeError,
    ProbeResult,
    SparqlClient,
    main,
    probe,
    q_classes,
    q_graphs,
    q_max_cardinality,
    q_predicates,
    q_sample_subject,
    q_subject_total,
    render_markdown,
)

GRAPH = "https://lindas.admin.ch/foj/zefix"
TARGET = "https://schema.ld.admin.ch/ZefixOrganisation"


class FakeClient(SparqlClient):
    """Antwortet aus einer Liste, ohne Netzwerk."""

    def __init__(self, answers: list[list[dict[str, Binding]]]) -> None:
        """Nimmt die Antworten in Aufrufreihenfolge entgegen."""
        super().__init__(endpoint="https://example.invalid/query")
        self._answers = answers
        self.asked: list[str] = []

    def query(self, sparql: str) -> list[dict[str, Binding]]:
        """Liefert die naechste vorbereitete Antwort."""
        self.asked.append(sparql)
        if not self._answers:
            raise AssertionError("mehr Abfragen als vorbereitete Antworten")
        return self._answers.pop(0)


def _iri(value: str) -> Binding:
    return Binding(value=value, kind="uri")


def _num(value: int) -> Binding:
    return Binding(value=str(value), kind="literal", datatype="http://www.w3.org/2001/XMLSchema#integer")


def _text(value: str, language: str | None = None) -> Binding:
    return Binding(value=value, kind="literal", language=language)


# -- Abfragen ------------------------------------------------------------- #


@pytest.mark.parametrize(
    "query",
    [
        q_graphs(5),
        q_classes(GRAPH, 5),
        q_subject_total(GRAPH, TARGET),
        q_predicates(GRAPH, TARGET, 5),
        q_max_cardinality(GRAPH, TARGET, "http://schema.org/name"),
        q_sample_subject(GRAPH, TARGET, 5),
    ],
)
def test_abfragen_sind_begrenzt_und_lesbar(query: str) -> None:
    assert query.startswith("SELECT")
    assert "DELETE" not in query.upper()
    assert "INSERT" not in query.upper()


def test_abfragen_tragen_ein_limit() -> None:
    """Ohne LIMIT waere jede Erhebung ein Vollscan auf fremder Infrastruktur."""
    for query in (q_graphs(5), q_classes(GRAPH, 5), q_predicates(GRAPH, TARGET, 5)):
        assert "LIMIT 5" in query


def test_zielgraph_und_klasse_werden_eingesetzt() -> None:
    assert f"<{GRAPH}>" in q_predicates(GRAPH, TARGET, 5)
    assert f"<{TARGET}>" in q_predicates(GRAPH, TARGET, 5)


# -- Auswertung ----------------------------------------------------------- #


def test_binding_parsen() -> None:
    binding = Binding.parse({"value": "Muster AG", "type": "literal", "xml:lang": "de"})
    assert (binding.value, binding.kind, binding.language) == ("Muster AG", "literal", "de")


def test_abdeckung_wird_berechnet() -> None:
    row = FieldRow(
        predicate="http://schema.org/name",
        occurrences=120,
        distinct_subjects=100,
        max_per_subject=2,
        value_kind="literal",
        datatype=None,
        sample_value="Muster AG",
    )
    assert row.coverage(200) == 0.5
    assert row.coverage(0) == 0.0


def test_erhebung_laeuft_durch() -> None:
    client = FakeClient(
        [
            [{"graph": _iri(GRAPH), "n": _num(9_000_000)}],           # Graphen
            [{"class": _iri(TARGET), "n": _num(700_000)}],            # Klassen
            [{"n": _num(700_000)}],                                   # Instanzzahl
            [                                                          # Praedikate
                {
                    "predicate": _iri("http://schema.org/name"),
                    "occurrences": _num(700_000),
                    "subjects": _num(700_000),
                    "sample": _text("Muster AG", "de"),
                }
            ],
            [{"max_per_subject": _num(1)}],                            # Kardinalitaet
            [{"predicate": _iri("http://schema.org/name"), "value": _text("Muster AG")}],
        ]
    )
    result = probe(
        client,
        graph=None,
        target_class=None,
        graph_hint="zefix",
        limit=5,
        cardinality_for=1,
        verbose=False,
    )
    assert result.graph == GRAPH
    assert result.target_class == TARGET
    assert result.subject_total == 700_000
    assert result.fields[0].max_per_subject == 1
    assert result.fields[0].coverage(result.subject_total) == 1.0
    assert len(result.queries) == 6


def test_fehlender_zielgraph_wird_gemeldet() -> None:
    client = FakeClient([[{"graph": _iri("https://example.org/anderes"), "n": _num(5)}]])
    with pytest.raises(ProbeError, match="Kein Graph enthaelt"):
        probe(
            client,
            graph=None,
            target_class=None,
            graph_hint="zefix",
            limit=5,
            cardinality_for=0,
            verbose=False,
        )


def test_graph_ohne_typisierte_subjekte_wird_gemeldet() -> None:
    client = FakeClient([[]])
    with pytest.raises(ProbeError, match="keine typisierten Subjekte"):
        probe(
            client,
            graph=GRAPH,
            target_class=None,
            graph_hint="zefix",
            limit=5,
            cardinality_for=0,
            verbose=False,
        )


def test_markdown_nennt_graph_klasse_und_felder() -> None:
    result = ProbeResult(
        endpoint="https://lindas.admin.ch/query",
        graph=GRAPH,
        target_class=TARGET,
        subject_total=100,
        graphs=[(GRAPH, 10)],
        classes=[(TARGET, 100)],
        fields=[
            FieldRow(
                predicate="http://schema.org/name",
                occurrences=100,
                distinct_subjects=100,
                max_per_subject=1,
                value_kind="literal",
                datatype="de",
                sample_value="Muster AG",
            )
        ],
        sample_subject=[("http://schema.org/name", "Muster AG")],
    )
    table = render_markdown(result)
    assert GRAPH in table
    assert "http://schema.org/name" in table
    assert "100.0%" in table


# -- Trockenlauf ---------------------------------------------------------- #


def test_trockenlauf_sendet_nichts(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Trockenlauf darf keine Verbindung oeffnen")

    monkeypatch.setattr("scripts.probe_lindas.urllib.request.urlopen", boom)
    assert main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "keine Netzwerkzugriffe" in out
    assert out.count("SELECT") >= 6


def test_json_ausgabe_ist_gueltig(tmp_path: Path) -> None:
    result = ProbeResult(endpoint="https://lindas.admin.ch/query", graph=GRAPH)
    from scripts.probe_lindas import _as_payload

    path = tmp_path / "felder.json"
    path.write_text(json.dumps(_as_payload(result), ensure_ascii=False), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["graph"] == GRAPH
