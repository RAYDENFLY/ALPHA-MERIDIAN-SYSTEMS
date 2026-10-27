using System.Collections.Generic;
using SignalGenerator.Desktop.Models;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class VelaBridgeContractTests
{
    private static readonly string[] Symbols = { "BTCUSDT" };
    private static readonly string[] Timeframes = { "1h" };

    [Fact]
    public void ValidSnapshot_IsAccepted()
    {
        var message = new VelaBridgeMessage(1, "vela.snapshot", "BTCUSDT", "1h", 1,
            new[] { new VelaCandleMessage(1000, 1, 2, 0.5m, 1.5m, 10) }, Array.Empty<VelaLevelMessage>(), Array.Empty<VelaSweepMessage>(), null, "LIVE");
        Assert.True(VelaBridgeValidator.TryValidate(message, Symbols, Timeframes, out _));
    }

    [Theory]
    [InlineData("vela.other")]
    [InlineData("vela.snapshot")]
    public void InvalidMessage_IsRejected(string type)
    {
        var candles = type == "vela.other"
            ? new[] { new VelaCandleMessage(1000, 1, 2, 0.5m, 1.5m, 10) }
            : new[] { new VelaCandleMessage(1000, 2, 1, 0.5m, 1.5m, 10) };
        var message = new VelaBridgeMessage(type == "vela.other" ? 1 : 2, type, "BTCUSDT", "1h", 1, candles, Array.Empty<VelaLevelMessage>(), Array.Empty<VelaSweepMessage>(), null, "LIVE");
        Assert.False(VelaBridgeValidator.TryValidate(message, Symbols, Timeframes, out _));
    }

    [Fact]
    public void NonMonotonicTimestamps_AreRejected()
    {
        var message = new VelaBridgeMessage(1, "vela.snapshot", "BTCUSDT", "1h", 1,
            new[] { new VelaCandleMessage(1001, 1, 2, 0.5m, 1.5m, 10), new VelaCandleMessage(1000, 1, 2, 0.5m, 1.5m, 10) }, Array.Empty<VelaLevelMessage>(), Array.Empty<VelaSweepMessage>(), null, "LIVE");
        Assert.False(VelaBridgeValidator.TryValidate(message, Symbols, Timeframes, out _));
    }
}
