using System;
using System.Collections.Generic;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Indicators;

public class StochasticResult
{
    public decimal?[] SmoothK { get; }
    public decimal?[] PercentD { get; }

    public StochasticResult(decimal?[] smoothK, decimal?[] percentD)
    {
        SmoothK = smoothK;
        PercentD = percentD;
    }
}

public static class TechnicalIndicators
{
    /// <summary>
    /// Calculates Exponential Moving Average (EMA) for a given period over a collection of candlesticks.
    /// Initialization convention: SMA of the first <paramref name="period"/> bars as the initial seed at index (period - 1).
    /// Recursive formula: EMA_t = alpha * Close_t + (1 - alpha) * EMA_(t-1), where alpha = 2 / (period + 1).
    /// Returns an array of nullable decimals of the same length as <paramref name="candles"/>, with null for bars before warm-up.
    /// </summary>
    public static decimal?[] ComputeEma(IReadOnlyList<KlineDto>? candles, int period)
    {
        if (candles == null || candles.Count == 0 || period <= 0)
        {
            return Array.Empty<decimal?>();
        }

        int count = candles.Count;
        var ema = new decimal?[count];

        if (count < period)
        {
            // Insufficient data for full initialization
            return ema;
        }

        decimal alpha = 2.0m / (period + 1.0m);

        // Compute SMA of the first 'period' candles for seed
        decimal sum = 0m;
        for (int i = 0; i < period; i++)
        {
            sum += candles[i].Close;
        }
        decimal currentEma = sum / period;
        ema[period - 1] = currentEma;

        // Recursive EMA computation
        for (int i = period; i < count; i++)
        {
            currentEma = (alpha * candles[i].Close) + ((1.0m - alpha) * currentEma);
            ema[i] = currentEma;
        }

        return ema;
    }

    /// <summary>
    /// Calculates Stochastic Oscillator (5, 3, 3).
    /// Lookback %K: 5 bars.
    /// %K Smoothing (Slow %K): 3 bars SMA of Raw %K.
    /// %D Smoothing: 3 bars SMA of Smoothed %K.
    /// Zero range handling: When HighestHigh == LowestLow, fallback to 50.0m (neutral midpoint).
    /// Returns StochasticResult containing arrays matching <paramref name="candles"/> length.
    /// </summary>
    public static StochasticResult ComputeStochastic(
        IReadOnlyList<KlineDto>? candles, 
        int kLookback = 5, 
        int kSmooth = 3, 
        int dSmooth = 3)
    {
        if (candles == null || candles.Count == 0)
        {
            return new StochasticResult(Array.Empty<decimal?>(), Array.Empty<decimal?>());
        }

        int count = candles.Count;
        var rawK = new decimal?[count];
        var smoothK = new decimal?[count];
        var percentD = new decimal?[count];

        if (count < kLookback)
        {
            return new StochasticResult(smoothK, percentD);
        }

        // Step 1: Calculate Raw %K
        for (int i = kLookback - 1; i < count; i++)
        {
            decimal lowestLow = decimal.MaxValue;
            decimal highestHigh = decimal.MinValue;

            for (int j = i - kLookback + 1; j <= i; j++)
            {
                if (candles[j].Low < lowestLow) lowestLow = candles[j].Low;
                if (candles[j].High > highestHigh) highestHigh = candles[j].High;
            }

            decimal range = highestHigh - lowestLow;
            if (range <= 0m)
            {
                // Fallback for zero range (flat price across lookback)
                rawK[i] = 50.0m;
            }
            else
            {
                decimal currentClose = candles[i].Close;
                decimal val = 100.0m * (currentClose - lowestLow) / range;
                rawK[i] = Math.Clamp(val, 0.0m, 100.0m);
            }
        }

        // Step 2: Calculate Smoothed %K (SMA of Raw %K over kSmooth bars)
        for (int i = 0; i < count; i++)
        {
            if (i < (kLookback - 1) + (kSmooth - 1))
            {
                smoothK[i] = null;
                continue;
            }

            decimal sumK = 0m;
            bool valid = true;
            for (int s = 0; s < kSmooth; s++)
            {
                var rk = rawK[i - s];
                if (!rk.HasValue)
                {
                    valid = false;
                    break;
                }
                sumK += rk.Value;
            }

            if (valid)
            {
                smoothK[i] = Math.Clamp(sumK / kSmooth, 0.0m, 100.0m);
            }
        }

        // Step 3: Calculate %D (SMA of Smoothed %K over dSmooth bars)
        for (int i = 0; i < count; i++)
        {
            if (i < (kLookback - 1) + (kSmooth - 1) + (dSmooth - 1))
            {
                percentD[i] = null;
                continue;
            }

            decimal sumD = 0m;
            bool valid = true;
            for (int s = 0; s < dSmooth; s++)
            {
                var sk = smoothK[i - s];
                if (!sk.HasValue)
                {
                    valid = false;
                    break;
                }
                sumD += sk.Value;
            }

            if (valid)
            {
                percentD[i] = Math.Clamp(sumD / dSmooth, 0.0m, 100.0m);
            }
        }

        return new StochasticResult(smoothK, percentD);
    }
}
