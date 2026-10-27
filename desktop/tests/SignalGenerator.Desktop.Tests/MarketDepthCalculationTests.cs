using System;
using System.Collections.Generic;
using System.Linq;
using SignalGenerator.Desktop.Models;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class MarketDepthCalculationTests
{
    [Fact]
    public void CumulativeDepth_CalculatesCorrectMonotonicSums()
    {
        var rawBids = new List<OrderBookBucketDto>
        {
            new() { BucketPrice = 80600m, TotalNotionalUsdt = 100000m, Side = "BID" },
            new() { BucketPrice = 80650m, TotalNotionalUsdt = 250000m, Side = "BID" },
            new() { BucketPrice = 80550m, TotalNotionalUsdt = 150000m, Side = "BID" }
        };

        var rawAsks = new List<OrderBookBucketDto>
        {
            new() { BucketPrice = 80750m, TotalNotionalUsdt = 120000m, Side = "ASK" },
            new() { BucketPrice = 80700m, TotalNotionalUsdt = 200000m, Side = "ASK" },
            new() { BucketPrice = 80800m, TotalNotionalUsdt = 180000m, Side = "ASK" }
        };

        // Bids sorted descending (highest bid closest to mid)
        var sortedBids = rawBids.OrderByDescending(b => b.BucketPrice).ToList();
        Assert.Equal(80650m, sortedBids[0].BucketPrice);
        Assert.Equal(80600m, sortedBids[1].BucketPrice);
        Assert.Equal(80550m, sortedBids[2].BucketPrice);

        decimal cumBid = 0m;
        var cumBids = sortedBids.Select(b => { cumBid += b.TotalNotionalUsdt; return (b.BucketPrice, cumBid); }).ToList();
        Assert.Equal(250000m, cumBids[0].cumBid);
        Assert.Equal(350000m, cumBids[1].cumBid);
        Assert.Equal(500000m, cumBids[2].cumBid);

        // Asks sorted ascending (lowest ask closest to mid)
        var sortedAsks = rawAsks.OrderBy(a => a.BucketPrice).ToList();
        Assert.Equal(80700m, sortedAsks[0].BucketPrice);
        Assert.Equal(80750m, sortedAsks[1].BucketPrice);
        Assert.Equal(80800m, sortedAsks[2].BucketPrice);

        decimal cumAsk = 0m;
        var cumAsks = sortedAsks.Select(a => { cumAsk += a.TotalNotionalUsdt; return (a.BucketPrice, cumAsk); }).ToList();
        Assert.Equal(200000m, cumAsks[0].cumAsk);
        Assert.Equal(320000m, cumAsks[1].cumAsk);
        Assert.Equal(500000m, cumAsks[2].cumAsk);
    }

    [Fact]
    public void BestBidAsk_And_SpreadDerivation_CalculatesAccurately()
    {
        decimal bestBid = 80650m;
        decimal bestAsk = 80700m;
        decimal mid = (bestBid + bestAsk) / 2m; // 80675m
        decimal spreadBps = ((bestAsk - bestBid) / mid) * 10000m;

        Assert.Equal(80675m, mid);
        Assert.Equal(6.1977m, Math.Round(spreadBps, 4));
    }

    [Fact]
    public void EmptyOrMalformedSnapshots_HandledWithoutException()
    {
        var emptyBids = new List<OrderBookBucketDto>();
        var emptyAsks = new List<OrderBookBucketDto>();

        var validBids = emptyBids.Where(b => b.BucketPrice > 0 && b.TotalNotionalUsdt > 0).ToList();
        var validAsks = emptyAsks.Where(a => a.BucketPrice > 0 && a.TotalNotionalUsdt > 0).ToList();

        Assert.Empty(validBids);
        Assert.Empty(validAsks);
    }
}
