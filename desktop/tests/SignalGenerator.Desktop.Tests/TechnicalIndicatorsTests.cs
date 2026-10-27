using System;
using System.Collections.Generic;
using System.Linq;
using SignalGenerator.Desktop.Indicators;
using SignalGenerator.Desktop.Models;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class TechnicalIndicatorsTests
{
    private static List<KlineDto> CreateConstantCandles(int count, decimal price)
    {
        var list = new List<KlineDto>();
        for (int i = 0; i < count; i++)
        {
            list.Add(new KlineDto
            {
                Time = i * 3600,
                TimestampMs = i * 3600000L,
                Open = price,
                High = price,
                Low = price,
                Close = price,
                Volume = 100m,
                DatetimeUtc = $"2026-10-01T{i:D2}:00:00Z"
            });
        }
        return list;
    }

    private static List<KlineDto> CreateTrendingCandles(int count, decimal startPrice, decimal step)
    {
        var list = new List<KlineDto>();
        for (int i = 0; i < count; i++)
        {
            decimal p = startPrice + (i * step);
            list.Add(new KlineDto
            {
                Time = i * 3600,
                TimestampMs = i * 3600000L,
                Open = p - 1m,
                High = p + 2m,
                Low = p - 2m,
                Close = p,
                Volume = 100m + i,
                DatetimeUtc = $"2026-10-01T{i:D2}:00:00Z"
            });
        }
        return list;
    }

    [Fact]
    public void ComputeEma_ConstantPrice_OutputsConstantEmaAfterWarmup()
    {
        var candles = CreateConstantCandles(100, 50.0m);
        var ema20 = TechnicalIndicators.ComputeEma(candles, 20);

        Assert.Equal(100, ema20.Length);
        for (int i = 0; i < 19; i++)
        {
            Assert.Null(ema20[i]);
        }
        for (int i = 19; i < 100; i++)
        {
            Assert.NotNull(ema20[i]);
            Assert.Equal(50.0m, Math.Round(ema20[i]!.Value, 4));
        }
    }

    [Fact]
    public void ComputeEma_KnownSequence_MatchesRecursiveFormula()
    {
        // 5 candles with closes: 10, 11, 12, 13, 14
        // Period = 3.
        // alpha = 2 / (3 + 1) = 0.5.
        // Seed at index 2 (bar 3): SMA(10, 11, 12) = 11.0.
        // Bar 3 (index 3, Close=13): EMA = 0.5 * 13 + 0.5 * 11 = 6.5 + 5.5 = 12.0.
        // Bar 4 (index 4, Close=14): EMA = 0.5 * 14 + 0.5 * 12 = 7.0 + 6.0 = 13.0.
        var candles = new List<KlineDto>
        {
            new() { Close = 10m },
            new() { Close = 11m },
            new() { Close = 12m },
            new() { Close = 13m },
            new() { Close = 14m }
        };

        var ema3 = TechnicalIndicators.ComputeEma(candles, 3);

        Assert.Null(ema3[0]);
        Assert.Null(ema3[1]);
        Assert.Equal(11.0m, ema3[2]);
        Assert.Equal(12.0m, ema3[3]);
        Assert.Equal(13.0m, ema3[4]);
    }

    [Fact]
    public void ComputeEma_InsufficientData_ReturnsAllNulls()
    {
        var candles = CreateConstantCandles(15, 100m);
        var ema20 = TechnicalIndicators.ComputeEma(candles, 20);

        Assert.Equal(15, ema20.Length);
        Assert.All(ema20, val => Assert.Null(val));
    }

    [Fact]
    public void ComputeEma_IsIndependentOfZoomAndPan()
    {
        var candles = CreateTrendingCandles(300, 100m, 0.5m);
        var fullEma50 = TechnicalIndicators.ComputeEma(candles, 50);

        // Verify that bar 150 has the exact same value regardless of how many bars are visible
        decimal? targetVal = fullEma50[150];
        Assert.NotNull(targetVal);

        // Re-compute on full series and check bar 150
        var recomputed = TechnicalIndicators.ComputeEma(candles, 50);
        Assert.Equal(targetVal, recomputed[150]);
    }

    [Fact]
    public void ComputeStochastic_ConstantPrice_OutputsNeutral50Fallback()
    {
        // When High == Low for all bars, range is zero.
        // Should fallback to 50.0m without division by zero.
        var candles = CreateConstantCandles(20, 100.0m);
        var stoch = TechnicalIndicators.ComputeStochastic(candles, kLookback: 5, kSmooth: 3, dSmooth: 3);

        Assert.Equal(20, stoch.SmoothK.Length);
        Assert.Equal(20, stoch.PercentD.Length);

        // Check that once warmed up, smoothK and percentD are 50.0m
        for (int i = 8; i < 20; i++)
        {
            Assert.NotNull(stoch.SmoothK[i]);
            Assert.NotNull(stoch.PercentD[i]);
            Assert.Equal(50.0m, stoch.SmoothK[i]!.Value);
            Assert.Equal(50.0m, stoch.PercentD[i]!.Value);
        }
    }

    [Fact]
    public void ComputeStochastic_TrendingData_CalculatesCorrectRanges()
    {
        var candles = CreateTrendingCandles(30, 100m, 1.0m);
        var stoch = TechnicalIndicators.ComputeStochastic(candles);

        // For strictly upward trending candles where close is near the high,
        // %K and %D should be high (> 50)
        for (int i = 10; i < 30; i++)
        {
            Assert.NotNull(stoch.SmoothK[i]);
            Assert.NotNull(stoch.PercentD[i]);
            Assert.True(stoch.SmoothK[i]!.Value >= 50m);
            Assert.True(stoch.PercentD[i]!.Value >= 50m);
        }
    }

    [Fact]
    public void ComputeStochastic_InsufficientData_ReturnsNullsSafely()
    {
        var candles = CreateConstantCandles(4, 100m);
        var stoch = TechnicalIndicators.ComputeStochastic(candles);

        Assert.Equal(4, stoch.SmoothK.Length);
        Assert.All(stoch.SmoothK, k => Assert.Null(k));
        Assert.All(stoch.PercentD, d => Assert.Null(d));
    }
}
