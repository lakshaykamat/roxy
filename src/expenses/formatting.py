"""Convert expense API results into concise, user-facing strings.

Formatting lives here so tool handlers stay thin and the exact wording is easy
to test. Amounts are rendered in Roxy's configured default currency; no currency
conversion is performed.
"""

from __future__ import annotations

from datetime import date

from src.expenses.models import Expense, ExpenseAnalysis

CURRENCY_SYMBOLS = {
    "INR": "₹",
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "JPY": "¥",
    "AUD": "A$",
    "CAD": "C$",
}


def format_amount(amount: float, currency: str) -> str:
    """Format an amount like ``₹2,480`` or ``₹4.50`` in the given currency."""
    symbol = CURRENCY_SYMBOLS.get(currency.upper())
    rounded = round(float(amount), 2)
    if rounded == int(rounded):
        number = f"{int(rounded):,}"
    else:
        number = f"{rounded:,.2f}"
    if symbol:
        return f"{symbol}{number}"
    return f"{number} {currency.upper()}"


def _pretty_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{parsed.strftime('%B')} {parsed.day}"


def format_created(expense: Expense, currency: str) -> str:
    parts = [f"Added {format_amount(expense.amount, currency)} for {expense.title}"]
    if expense.category:
        parts.append(f"under {expense.category}")
    parts.append(f"for {_pretty_date(expense.date)}.")
    return " ".join(parts)


def format_bulk_upsert(created: int, updated: int) -> str:
    parts: list[str] = []
    if created:
        parts.append(f"added {created} expense{'s' if created != 1 else ''}")
    if updated:
        parts.append(f"updated {updated} expense{'s' if updated != 1 else ''}")
    return f"Bulk save complete, {' and '.join(parts)}." if parts else "Bulk save complete."


def format_expense_line(index: int, expense: Expense, currency: str) -> str:
    pieces = [f"{index}. {expense.title}", format_amount(expense.amount, currency)]
    if expense.category:
        pieces.append(expense.category)
    pieces.append(_pretty_date(expense.date))
    return " — ".join(pieces)


def format_expense_list(expenses: list[Expense], currency: str, label: str = "") -> str:
    if not expenses:
        return "I couldn't find any expenses for that period."
    heading = f"Your latest expenses{f' for {label}' if label else ''}:"
    lines = [
        format_expense_line(index, expense, currency)
        for index, expense in enumerate(expenses, start=1)
    ]
    total = sum(expense.amount for expense in expenses)
    return f"{heading}\n\n" + "\n".join(lines) + f"\n\nTotal: {format_amount(total, currency)}"


def format_category_summary(
    groups: list[dict[str, object]], currency: str, label: str = "This month"
) -> str:
    if not groups:
        return "I couldn't find any expenses for that period."
    lines = [
        f"{group.get('category', 'Uncategorised')}: "
        f"{format_amount(float(group.get('total', 0)), currency)}"
        for group in groups
    ]
    total = sum(float(group.get("total", 0)) for group in groups)
    return f"{label}:\n\n" + "\n".join(lines) + f"\n\nTotal: {format_amount(total, currency)}"


def format_expense_analysis(analysis: ExpenseAnalysis, currency: str, label: str) -> str:
    heading = f"{label} analysis" if label else "Monthly expense analysis"
    lines = [
        heading + ":",
        f"Total spent: {format_amount(analysis.total_expenses, currency)}",
        f"Daily average: {format_amount(analysis.daily_average_spend, currency)}",
    ]
    if analysis.budget_exists:
        lines.extend([
            f"Budget: {format_amount(analysis.total_budget, currency)}",
            f"Remaining budget: {format_amount(analysis.remaining_budget, currency)}",
            f"Budget used: {analysis.budget_used_percentage:g}%",
        ])
    else:
        lines.append("Your spending was analysed, but no monthly budget is set.")
    if analysis.top_categories:
        lines.append("\nTop categories:")
        lines.extend(
            f"{category.category}: {format_amount(category.amount, currency)}"
            for category in analysis.top_categories
        )
    if analysis.top_expenses:
        lines.append("\nTop expenses:")
        lines.extend(
            f"{expense.title}: {format_amount(expense.amount, currency)}"
            for expense in analysis.top_expenses
        )
    if analysis.weekly_expenses:
        lines.append("\nWeekly spending:")
        lines.extend(
            f"{_pretty_date(expense.start_date)} to {_pretty_date(expense.end_date)}: "
            f"{format_amount(expense.amount, currency)}"
            for expense in analysis.weekly_expenses
        )
    return "\n".join(lines)


def format_updated(before: Expense, after: Expense, currency: str) -> str:
    if before.amount != after.amount:
        return (
            f"Updated {after.title} from {format_amount(before.amount, currency)} "
            f"to {format_amount(after.amount, currency)}."
        )
    return f"Updated {after.title}."


def format_deleted(expense: Expense, currency: str) -> str:
    return f"Deleted “{expense.title}” for {format_amount(expense.amount, currency)}."


def summarize_expense(expense: Expense, currency: str) -> str:
    """A short one-line identifier used in confirmations and candidate lists."""
    pieces = [expense.title, format_amount(expense.amount, currency)]
    if expense.category:
        pieces.append(expense.category)
    pieces.append(_pretty_date(expense.date))
    return " — ".join(pieces)
