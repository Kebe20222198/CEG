"""Figures et tableaux du rapport, à partir de resultats/*.json.

    .venv/bin/python scripts/figures.py            # après scripts/reproduire.sh

Écrit dans resultats/figures/ (PDF vectoriels, pour LaTeX) et
resultats/tableaux/ (fragments LaTeX à inclure avec \\input) : les chiffres
du rapport viennent ainsi directement des résultats versionnés.

Choix de forme (figures imprimées, donc statiques) :
  - benchmark : petits multiples, une ligne par contrainte (coût, latence,
    qualité rapportés à leur limite), une colonne par scénario ; la ligne
    pointillée est la contrainte. CEG en bleu, les modèles fixes en gris :
    la couleur met en avant CEG, l'identité est portée par les étiquettes.
  - optimiseur appris : évolution sur les exécutions (lignes), coût par
    exécution et appels ratés cumulés, sur deux axes séparés.
"""

from __future__ import annotations

import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from itertools import accumulate
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "resultats"
FIGURES = RESULTS / "figures"
TABLES = RESULTS / "tableaux"

# Palette validée (scripts de dataviz) : deux teintes, encre et grille neutres.
BLUE = "#2a78d6"  # CEG / optimiseur appris
ORANGE = "#eb6834"  # optimiseur statique
GRAY = "#b4b2a9"  # modèles fixes (contexte)
INK = "#0b0b0b"
INK_2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"

CM = 1 / 2.54
WIDTH = 16 * CM  # largeur du texte d'une page A4 à marges usuelles

SCENARIO_LABELS = {
    "large": "Large",
    "moyen": "Moyen",
    "serre": "Serré",
    "exigeant": "Exigeant",
}
STRATEGY_LABELS = {
    "fixe:fast-mini": "fast-mini (fixe)",
    "fixe:balanced-standard": "balanced-standard (fixe)",
    "fixe:quality-pro": "quality-pro (fixe)",
    "ceg:static": "CEG statique",
    "ceg:learned": "CEG appris",
}


def fr(value: float, digits: int = 2) -> str:
    """Nombre à la française, arrondi au plus proche (0,5 vers le haut)."""
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:f}".replace(".", ",")


def tex_num(value: float, digits: int = 2) -> str:
    """Nombre pour LaTeX : virgule protégée en mode math, vrai signe moins."""
    text = fr(abs(value), digits).replace(",", "{,}")
    return f"$-{text}$" if value < 0 else f"${text}$"


def style() -> None:
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.size": 7.5,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.6,
            "axes.labelcolor": INK_2,
            "axes.titlesize": 8,
            "axes.titleweight": "bold",
            "axes.titlecolor": INK,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "xtick.labelcolor": INK_2,
            "ytick.labelcolor": INK_2,
            "xtick.major.width": 0.6,
            "ytick.major.width": 0.6,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,  # polices incorporées, texte sélectionnable
        }
    )


# ── Benchmark ─────────────────────────────────────────────────────────────────


def benchmark_figure(report: dict) -> Path:
    scenarios = list(report["results"])
    strategies = list(next(iter(report["results"].values())))
    rows = [
        ("cost_usd", "max_cost_usd", "Coût / budget", 1.0),
        ("latency_ms", "max_latency_seconds", "Latence / limite", 1000.0),
        ("quality", "min_quality_score", "Qualité livrée", None),
    ]
    clip = 2.2  # au-delà, la barre est coupée et sa valeur écrite

    fig, axes = plt.subplots(
        len(rows),
        len(scenarios),
        figsize=(WIDTH, 12 * CM),
        sharey=True,
        gridspec_kw={"hspace": 0.6, "wspace": 0.22},
    )
    y = list(range(len(strategies)))[::-1]
    for r, (metric, limit_key, label, scale) in enumerate(rows):
        for c, scenario in enumerate(scenarios):
            ax = axes[r][c]
            limits = report["scenarios"][scenario]
            limit = limits[limit_key]
            values = []
            for strategy in strategies:
                raw = report["results"][scenario][strategy][metric]
                values.append(raw / (limit * scale) if scale else raw)
            colors = [BLUE if s.startswith("ceg") else GRAY for s in strategies]
            shown = [min(v, clip) for v in values]
            ax.barh(
                y, shown, height=0.62, color=colors, edgecolor="#fcfcfb", linewidth=0.8
            )
            reference = 1.0 if scale else limit
            ax.axvline(reference, color=INK_2, linewidth=0.8, linestyle=(0, (3, 2)))
            ax.grid(axis="x", color=GRID, linewidth=0.5)
            ax.set_axisbelow(True)
            ax.tick_params(axis="y", length=0)
            if scale:
                ax.set_xlim(0, clip * 1.05)
                for yi, v in zip(y, values, strict=True):
                    if v > clip:
                        ax.text(
                            clip * 0.97,
                            yi,
                            f"{fr(v, 1)}×",
                            va="center",
                            ha="right",
                            color="#ffffff",
                            fontsize=6.5,
                            fontweight="bold",
                        )
                ax.set_xticks([0, 1, 2])
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}×"))
            else:
                ax.set_xlim(0, 1.0)
                ax.set_xticks([0, 0.5, 1.0])
                ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fr(v, 1)))
            if r == 0:
                ax.set_title(SCENARIO_LABELS.get(scenario, scenario), pad=16)
                ax.text(
                    0.5,
                    1.04,
                    f"{fr(limits['max_cost_usd'])} $ · "
                    f"{fr(limits['max_latency_seconds'], 0)} s · "
                    f"q ≥ {fr(limits['min_quality_score'])}",
                    transform=ax.transAxes,
                    ha="center",
                    va="bottom",
                    color=INK_2,
                    fontsize=6.5,
                )
            if c == 0:
                ax.set_ylabel(label, fontsize=7.5, fontweight="bold", color=INK)
                ax.set_yticks(y, [STRATEGY_LABELS.get(s, s) for s in strategies])
    fig.text(
        0.5,
        0.005,
        "Ligne pointillée : contrainte déclarée (coût et latence : à gauche = "
        "respectée ; qualité : à droite = respectée).",
        ha="center",
        color=MUTED,
        fontsize=6.5,
    )
    path = FIGURES / "benchmark.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def benchmark_table(report: dict) -> Path:
    scenarios = list(report["results"])
    strategies = list(next(iter(report["results"].values())))
    limits = report["scenarios"]

    def row(label: str, cells: list[str]) -> str:
        return f"{label} & " + " & ".join(cells) + " \\\\"

    lines = [
        "% Généré par scripts/figures.py depuis resultats/benchmark.json",
        "\\begin{tabular}{l" + "c" * len(scenarios) + "}",
        "\\toprule",
        row(
            "\\textbf{Stratégie}",
            [f"\\textbf{{{SCENARIO_LABELS.get(s, s)}}}" for s in scenarios],
        ),
        row(
            "\\quad\\textit{budget}",
            [f"{tex_num(limits[s]['max_cost_usd'])}\\,\\$" for s in scenarios],
        ),
        row(
            "\\quad\\textit{latence max.}",
            [f"{tex_num(limits[s]['max_latency_seconds'], 0)}\\,s" for s in scenarios],
        ),
        row(
            "\\quad\\textit{qualité min.}",
            [tex_num(limits[s]["min_quality_score"]) for s in scenarios],
        ),
        "\\midrule",
    ]
    for strategy in strategies:
        cells = []
        for s in scenarios:
            pct = f"{round(report['results'][s][strategy]['respect'] * 100)}\\,\\%"
            cells.append(f"\\textbf{{{pct}}}" if strategy.startswith("ceg") else pct)
        name = STRATEGY_LABELS.get(strategy, strategy)
        if strategy.startswith("ceg"):
            name = f"\\textbf{{{name}}}"
        lines.append(row(name, cells))
    lines += ["\\bottomrule", "\\end{tabular}", ""]
    path = TABLES / "benchmark.tex"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ── Optimiseur appris ─────────────────────────────────────────────────────────


def optimizer_figure(report: dict) -> Path:
    results = report["results"]
    series = [("static", "Statique", ORANGE), ("learned", "Appris", BLUE)]
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(WIDTH, 5.5 * CM), gridspec_kw={"wspace": 0.3}
    )
    for key, label, color in series:
        runs = results[key]["per_run"]
        x = list(range(1, len(runs) + 1))
        cost = [r["cost_usd"] for r in runs]
        failed = list(accumulate(r["failed_calls"] for r in runs))
        left.plot(x, cost, color=color, linewidth=1.6, label=label)
        right.plot(x, failed, color=color, linewidth=1.6, label=label)
        left.text(x[-1] + 0.8, cost[-1], label, color=INK_2, va="center", fontsize=7)
        right.text(x[-1] + 0.8, failed[-1], label, color=INK_2, va="center", fontsize=7)

    left.set_title("Coût par exécution", loc="left")
    left.set_ylabel("USD")
    left.set_ylim(bottom=0)
    left.yaxis.set_major_formatter(FuncFormatter(lambda v, _: fr(v, 3)))
    right.set_title("Appels ratés cumulés", loc="left")
    right.set_ylim(bottom=0)
    for ax in (left, right):
        ax.set_xlabel("Exécution")
        ax.set_xlim(1, len(results["static"]["per_run"]) + 6)
        ax.grid(axis="y", color=GRID, linewidth=0.5)
        ax.set_axisbelow(True)
    left.legend(frameon=False, loc="lower left", fontsize=7)
    path = FIGURES / "optimiseur_appris.pdf"
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    return path


def optimizer_table(report: dict) -> Path:
    results = report["results"]
    static = results["static"]["all_runs"]
    learned = results["learned"]["all_runs"]

    def delta(key: str) -> str:
        change = (learned[key] - static[key]) / static[key] * 100
        return f"({tex_num(change, 0)}\\,\\%)"

    lines = [
        "% Généré par scripts/figures.py depuis resultats/optimiseur_appris.json",
        "\\begin{tabular}{lrrrr}",
        "\\toprule",
        "\\textbf{Optimiseur} & \\textbf{Coût / exéc.} & \\textbf{Latence} "
        "& \\textbf{Appels ratés} & \\textbf{Qualité livrée} \\\\",
        "\\midrule",
        f"Statique & {fr(static['cost_usd'], 4)}\\,\\$ & "
        f"{fr(static['latency_ms'], 0)}\\,ms & {fr(static['failed_calls'])} & "
        f"{fr(static['quality'], 3)} \\\\",
        "\\textbf{Appris} & "
        f"{fr(learned['cost_usd'], 4)}\\,\\$ {delta('cost_usd')} & "
        f"{fr(learned['latency_ms'], 0)}\\,ms {delta('latency_ms')} & "
        f"{fr(learned['failed_calls'])} {delta('failed_calls')} & "
        f"{fr(learned['quality'], 3)} \\\\",
        "\\bottomrule",
        "\\end{tabular}",
        "",
    ]
    path = TABLES / "optimiseur_appris.tex"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    missing = [
        name
        for name in ("benchmark.json", "optimiseur_appris.json")
        if not (RESULTS / name).exists()
    ]
    if missing:
        print(f"✗ Manquant dans resultats/ : {', '.join(missing)}")
        print("  Lancez d'abord ./scripts/reproduire.sh")
        return 1
    FIGURES.mkdir(parents=True, exist_ok=True)
    TABLES.mkdir(parents=True, exist_ok=True)
    style()
    benchmark = json.loads((RESULTS / "benchmark.json").read_text(encoding="utf-8"))
    optimizer = json.loads(
        (RESULTS / "optimiseur_appris.json").read_text(encoding="utf-8")
    )
    for path in (
        benchmark_figure(benchmark),
        benchmark_table(benchmark),
        optimizer_figure(optimizer),
        optimizer_table(optimizer),
    ):
        print(f"✓ {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
