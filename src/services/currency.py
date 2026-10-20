from services import db, fx

# Matches the website's existing convention for non-USD tickers, a currency code prefix/suffix
# instead of a symbol, since most currencies don't have a clean single-character symbol anyway.
def fmt_native(amount: float, currency: str | None) -> str:
    currency = (currency or "USD").upper()
    if currency == "USD":
        return f"${amount:,.2f}"
    return f"{amount:,.2f} {currency}"


def fmt_native_padded(amount: float, cur: str | None, width: int = 8) -> str:
    # Same numeric width either way, used inside fixed-width ansi tables where a non-USD row (rare)
    # just trades the $ prefix for a currency suffix instead of throwing off the column alignment.
    cur = (cur or "USD").upper()
    if cur == "USD":
        return f"${amount:>{width}.2f}"
    return f"{amount:>{width}.2f} {cur}"


async def fmt_price(amount: float, currency: str | None, guild_id: int, user_id: int) -> str:
    # The 0.1% case: someone turned on /currency set, convert and show both so it's never ambiguous.
    currency = (currency or "USD").upper()
    pref = await db.get_currency_pref(guild_id, user_id)
    if not pref or pref == currency:
        return fmt_native(amount, currency)

    rate = await fx.get_fx_rate(currency, pref)
    if rate is None:
        return fmt_native(amount, currency)

    converted = amount * rate
    return f"{fmt_native(converted, pref)} ({fmt_native(amount, currency)})"
