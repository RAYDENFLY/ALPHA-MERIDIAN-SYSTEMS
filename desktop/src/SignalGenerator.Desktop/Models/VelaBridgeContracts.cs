using System;
using System.Collections.Generic;
using System.Linq;

namespace SignalGenerator.Desktop.Models;

public sealed record VelaBridgeMessage(
    int Version,
    string Type,
    string Symbol,
    string Timeframe,
    long Sequence,
    IReadOnlyList<VelaCandleMessage> Candles,
    string State,
    string? Error = null);

public sealed record VelaCandleMessage(long Time, decimal Open, decimal High, decimal Low, decimal Close, decimal Volume);

public static class VelaBridgeValidator
{
    public static bool TryValidate(VelaBridgeMessage? message, IReadOnlyCollection<string> supportedSymbols, IReadOnlyCollection<string> supportedTimeframes, out string error)
    {
        error = string.Empty;
        if (message == null || message.Version != 1) { error = "Unsupported bridge version."; return false; }
        if (!string.Equals(message.Type, "vela.snapshot", StringComparison.Ordinal)) { error = "Unsupported bridge message type."; return false; }
        if (!supportedSymbols.Contains(message.Symbol, StringComparer.OrdinalIgnoreCase)) { error = "Unsupported symbol."; return false; }
        if (!supportedTimeframes.Contains(message.Timeframe, StringComparer.OrdinalIgnoreCase)) { error = "Unsupported timeframe."; return false; }
        if (message.Candles.Any(c => c.Time <= 0 || c.High < Math.Max(c.Open, c.Close) || c.Low > Math.Min(c.Open, c.Close) || c.Volume < 0)) { error = "Invalid candle values."; return false; }
        if (message.Candles.Zip(message.Candles.Skip(1), (a, b) => (a, b)).Any(pair => pair.b.Time <= pair.a.Time)) { error = "Candle timestamps are not strictly increasing."; return false; }
        return true;
    }
}
