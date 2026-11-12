"""Plotly figures for the dashboard. Presentation only: no financial logic.

Colour roles: fixed categorical order (blue, orange, aqua) follows the entity
(tranche / scenario), one hue for single-series magnitude, and a blue-grey-red
diverging scale centred on the target IRR for heatmaps. One y-axis per chart.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go

SURFACE = "#fcfcfb"
INK, INK2, MUTED = "#0b0b0b", "#52514e", "#898781"
GRID, AXIS = "#e1e0d9", "#c3c2b7"
BLUE, ORANGE, AQUA, YELLOW = "#2a78d6", "#eb6834", "#1baf7a", "#eda100"
GOOD, CRITICAL = "#0ca30c", "#d03b3b"
NEUTRAL = "#898781"
TRANCHE_COLOURS = {"senior": BLUE, "sub": ORANGE, "revolver": AQUA}
TRANCHE_NAMES = {"senior": "Senior term loan", "sub": "Subordinated debt", "revolver": "Revolver"}
SCENARIO_COLOURS = {"Bull": AQUA, "Base": BLUE, "Bear": ORANGE}
DIVERGING = [[0.0, "#d03b3b"], [0.25, "#eda3a0"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]]
SEQUENTIAL = [[0.0, "#cde2fb"], [0.5, "#5598e7"], [1.0, "#104281"]]
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def _layout(fig: go.Figure, title: str, height: int = 320, yfmt: str = ",.0f", legend: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=14, color=INK), x=0, xanchor="left"),
        font=dict(family=FONT, size=12, color=INK2), paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        height=height, margin=dict(l=10, r=10, t=48, b=10), bargap=0.35,
        showlegend=legend, legend=dict(orientation="h", y=-0.16, x=0, font=dict(color=INK2)),
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, color=INK)),
    )
    fig.update_xaxes(showgrid=False, linecolor=AXIS, tickfont=dict(color=MUTED))
    fig.update_yaxes(gridcolor=GRID, zerolinecolor=AXIS, tickformat=yfmt, tickfont=dict(color=MUTED))
    return fig


def _bar(x: Sequence, y: Sequence[float], name: str, colour: str, hover: str = "%{y:,.1f}", text: Sequence[str] | None = None) -> go.Bar:
    return go.Bar(x=list(x), y=list(y), name=name, marker=dict(color=colour, line=dict(color=SURFACE, width=2)),
                  hovertemplate=f"<b>{name}</b><br>%{{x}}: {hover}<extra></extra>", text=text, textposition="outside",
                  textfont=dict(color=INK2, size=11), cliponaxis=False)


def ebitda_chart(op, years: int) -> go.Figure:
    x = ["LTM"] + [f"Year {t}" for t in range(1, years + 1)]
    y = op.ebitda[:years + 1]
    fig = go.Figure(go.Bar(
        x=x, y=y, name="EBITDA", marker=dict(color=BLUE, line=dict(color=SURFACE, width=2)),
        customdata=np.stack([op.margin[:years + 1], op.revenue[:years + 1]], axis=-1),
        text=[f"{v:,.0f}" for v in y], textposition="outside", textfont=dict(color=INK2, size=11), cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>EBITDA %{y:,.1f}<br>Margin %{customdata[0]:.1%}<br>Revenue %{customdata[1]:,.1f}<extra></extra>"))
    return _layout(fig, "EBITDA development", legend=False)


def debt_balance_chart(ds, years: int) -> go.Figure:
    x = ["Close"] + [f"Year {t}" for t in range(1, years + 1)]
    fig = go.Figure()
    for k in ("senior", "sub", "revolver"):
        bal = ds.tranches[k].closing[:years + 1]
        if bal.max() > 1e-9:
            fig.add_trace(_bar(x, bal, TRANCHE_NAMES[k], TRANCHE_COLOURS[k]))
    fig.add_trace(go.Scatter(x=x, y=ds.total_debt[:years + 1], mode="text", showlegend=False, hoverinfo="skip",
                             text=[f"{lev:.1f}x" for lev in ds.leverage[:years + 1]], textposition="top center",
                             textfont=dict(color=INK2, size=11)))
    fig.update_layout(barmode="stack")
    fig.update_yaxes(range=[0, float(ds.total_debt[:years + 1].max()) * 1.12])   # headroom for the leverage labels
    return _layout(fig, "Debt balance by tranche (label: total debt / EBITDA)")


def repayment_chart(ds, years: int) -> go.Figure:
    x = [f"Year {t}" for t in range(1, years + 1)]
    sl = slice(1, years + 1)
    fig = go.Figure([
        _bar(x, ds.total_mandatory[sl], "Mandatory repayment", BLUE),
        _bar(x, ds.total_optional[sl], "Optional repayment (sweep)", ORANGE),
    ])
    fig.update_layout(barmode="stack")
    return _layout(fig, "Debt repayment schedule")


def debt_service_chart(ds, years: int) -> go.Figure:
    x = [f"Year {t}" for t in range(1, years + 1)]
    sl = slice(1, years + 1)
    fig = go.Figure([
        _bar(x, ds.cfads[sl], "Cash flow available for debt service", BLUE),
        _bar(x, ds.total_interest[sl] + ds.total_mandatory[sl], "Interest + mandatory repayments", ORANGE),
    ])
    fig.update_layout(barmode="group", bargroupgap=0.08)
    return _layout(fig, "Debt service capacity")


def capital_structure_chart(su) -> go.Figure:
    parts = [("Senior term loan", su.senior_debt, BLUE), ("Subordinated debt", su.sub_debt, ORANGE),
             ("Revolver", su.revolver_draw, AQUA), ("Rollover equity", su.rollover_equity, YELLOW),
             ("Sponsor equity", su.sponsor_equity, "#6250d6")]
    total = su.total_debt + su.total_equity
    fig = go.Figure()
    for name, v, c in parts:
        if v > 1e-9:
            fig.add_trace(go.Bar(y=["Capitalisation"], x=[v], name=name, orientation="h",
                                 marker=dict(color=c, line=dict(color=SURFACE, width=2)),
                                 text=[f"{v / total:.0%}"], textposition="inside", insidetextanchor="middle",
                                 textfont=dict(color="#0b0b0b" if c == YELLOW else "white"),
                                 hovertemplate=f"<b>{name}</b><br>{v:,.1f} ({v / total:.1%}; {v / su.entry_ebitda:.2f}x EBITDA)<extra></extra>"))
    fig.update_layout(barmode="stack")
    _layout(fig, "Capital structure at close", height=190)
    fig.update_yaxes(showticklabels=False, showgrid=False)
    fig.update_xaxes(showgrid=True, gridcolor=GRID, tickformat=",.0f")
    return fig


def waterfall(title: str, steps: list[tuple[str, float, str]], height: int = 340) -> go.Figure:
    """steps: (label, value, 'absolute' | 'relative' | 'total')."""
    fig = go.Figure(go.Waterfall(
        x=[s[0] for s in steps], y=[s[1] for s in steps], measure=[s[2] for s in steps],
        text=[f"{s[1]:,.0f}" for s in steps], textposition="outside", textfont=dict(color=INK2, size=11),
        connector=dict(line=dict(color=AXIS, width=1)),
        increasing=dict(marker=dict(color=BLUE)), decreasing=dict(marker=dict(color=CRITICAL)),
        totals=dict(marker=dict(color=NEUTRAL)), cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>%{y:,.1f}<extra></extra>"))
    return _layout(fig, title, height=height, legend=False)


def heatmap(df: pd.DataFrame, title: str, metric: str, centre: float) -> go.Figure:
    z = df.values
    is_irr = metric == "IRR"
    text = [[("n/a" if np.isnan(v) else (f"{v:.1%}" if is_irr else f"{v:.2f}x")) for v in row] for row in z]
    span = max(float(np.nanmax(np.abs(z - centre))) if np.isfinite(z).any() else 1.0, 1e-6)
    fig = go.Figure(go.Heatmap(
        z=z, x=list(df.columns), y=list(df.index), text=text, texttemplate="%{text}",
        textfont=dict(size=11), colorscale=DIVERGING, zmid=centre, zmin=centre - span, zmax=centre + span,
        xgap=2, ygap=2, showscale=False,
        hovertemplate=f"{df.index.name}: %{{y}}<br>{df.columns.name}: %{{x}}<br><b>{metric} %{{text}}</b><extra></extra>"))
    _layout(fig, title, height=360, legend=False)
    fig.update_xaxes(title=dict(text=df.columns.name, font=dict(color=INK2)), side="bottom", type="category")
    fig.update_yaxes(title=dict(text=df.index.name, font=dict(color=INK2)), autorange="reversed", type="category",
                     showgrid=False, tickformat=None)
    return fig


def scenario_bars(values: Mapping[str, float], title: str, fmt: str) -> go.Figure:
    names = list(values)
    is_pct = "%" in fmt
    fig = go.Figure(go.Bar(
        x=names, y=[values[n] for n in names],
        marker=dict(color=[SCENARIO_COLOURS.get(n, NEUTRAL) for n in names], line=dict(color=SURFACE, width=2)),
        text=[format(values[n], fmt) + ("" if is_pct else "x" if "MOIC" in title else "") for n in names],
        textposition="outside", textfont=dict(color=INK2), cliponaxis=False,
        hovertemplate="<b>%{x}</b><br>%{text}<extra></extra>"))
    return _layout(fig, title, height=280, yfmt=".0%" if is_pct else ",.1f", legend=False)


def scenario_lines(series: Mapping[str, Sequence[float]], title: str, yfmt: str = ".1f", suffix: str = "x") -> go.Figure:
    fig = go.Figure()
    for name, ys in series.items():
        x = ["Close"] + [f"Year {t}" for t in range(1, len(ys))]
        fig.add_trace(go.Scatter(x=x, y=list(ys), name=name, mode="lines+markers",
                                 line=dict(color=SCENARIO_COLOURS.get(name, NEUTRAL), width=2), marker=dict(size=8),
                                 hovertemplate=f"<b>{name}</b><br>%{{x}}: %{{y:{yfmt}}}{suffix}<extra></extra>"))
    fig.update_layout(hovermode="x unified")
    return _layout(fig, title, height=300, yfmt=yfmt)


def simple_bars(x: Sequence, y: Sequence[float], title: str, name: str, pct_axis: bool = False,
                labels: Sequence[str] | None = None, height: int = 280) -> go.Figure:
    fig = go.Figure(_bar(x, y, name, BLUE, hover="%{y:.1%}" if pct_axis else "%{y:,.2f}", text=labels))
    fig.update_xaxes(type="category")
    return _layout(fig, title, height=height, yfmt=".0%" if pct_axis else ",.1f", legend=False)


def sponsor_cash_flows(dates: Sequence, flows: Sequence[float]) -> go.Figure:
    x = [f"{d:%b %Y}" for d in dates]
    fig = go.Figure(go.Bar(
        x=x, y=list(flows), marker=dict(color=[ORANGE if v < 0 else BLUE for v in flows], line=dict(color=SURFACE, width=2)),
        text=[f"{v:,.0f}" if abs(v) > 1e-9 else "" for v in flows], textposition="outside", textfont=dict(color=INK2),
        cliponaxis=False, hovertemplate="<b>%{x}</b><br>%{y:,.1f}<extra></extra>"))
    return _layout(fig, "Sponsor cash flows (investment negative, proceeds positive)", height=300, legend=False)
