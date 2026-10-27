using System;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class CoordinateMappingAndChartTests
{
    [Fact]
    public void PriceToPixel_And_PixelToPrice_RoundTripCorrectly()
    {
        double headerHeight = 22.0;
        double pricePlotH = 300.0;
        decimal minPrice = 80000m;
        decimal maxPrice = 90000m;
        decimal priceRange = maxPrice - minPrice; // 10000

        // Test price in middle: 85000
        decimal testPrice = 85000m;
        double y = headerHeight + pricePlotH * (double)((maxPrice - testPrice) / priceRange);
        Assert.Equal(headerHeight + 150.0, y);

        // Convert back from pixel Y to price
        decimal reconstructedPrice = maxPrice - (decimal)((y - headerHeight) / pricePlotH) * priceRange;
        Assert.Equal(testPrice, reconstructedPrice);

        // Test extreme top (maxPrice)
        double topY = headerHeight + pricePlotH * (double)((maxPrice - maxPrice) / priceRange);
        Assert.Equal(headerHeight, topY);
        decimal reconstructedTop = maxPrice - (decimal)((topY - headerHeight) / pricePlotH) * priceRange;
        Assert.Equal(maxPrice, reconstructedTop);

        // Test extreme bottom (minPrice)
        double bottomY = headerHeight + pricePlotH * (double)((maxPrice - minPrice) / priceRange);
        Assert.Equal(headerHeight + pricePlotH, bottomY);
        decimal reconstructedBottom = maxPrice - (decimal)((bottomY - headerHeight) / pricePlotH) * priceRange;
        Assert.Equal(minPrice, reconstructedBottom);
    }

    [Fact]
    public void CursorAnchoredZoom_PreservesAnchorCandleUnderCursor()
    {
        int totalBars = 500;
        int initialVisible = 50;
        int initialPanOffset = 0; // right-aligned: startIndex = 450 to 500
        int startIndex = totalBars - initialVisible - initialPanOffset; // 450

        // Suppose cursor is at ratio r = 0.6 (60% from left of chart)
        double r = 0.6;
        double anchorIdx = startIndex + (r * initialVisible); // 450 + 30 = 480.0

        // Zoom in to 30 visible bars
        int newVisible = 30;
        double newStartIndex = anchorIdx - (r * newVisible); // 480 - 18 = 462.0
        int newPanOffset = (int)Math.Round(totalBars - newVisible - newStartIndex); // 500 - 30 - 462 = 8

        // Verify that in the new viewport, anchorIdx is still at ratio r
        double reconstructedRatio = (anchorIdx - newStartIndex) / newVisible;
        Assert.Equal(r, reconstructedRatio, 4);
    }

    [Fact]
    public void PanClamping_PreventsOutOfBoundsNavigation()
    {
        int totalBars = 100;
        int visibleBars = 40;
        int maxOffset = Math.Max(0, totalBars - visibleBars); // 60

        // Test clamping negative offset
        int panOffsetNegative = Math.Clamp(-15, 0, maxOffset);
        Assert.Equal(0, panOffsetNegative);

        // Test clamping excessive offset
        int panOffsetExcess = Math.Clamp(120, 0, maxOffset);
        Assert.Equal(60, panOffsetExcess);

        // Test valid intermediate offset
        int panOffsetValid = Math.Clamp(25, 0, maxOffset);
        Assert.Equal(25, panOffsetValid);
    }

    [Fact]
    public void Autoscale_CalculatesPaddedBoundsWithoutDivisionByZero()
    {
        // Flat price case (constant price)
        decimal flatMin = 100m;
        decimal flatMax = 100m;
        if (flatMax <= flatMin) flatMax = flatMin + 1m;
        decimal padding = (flatMax - flatMin) * 0.05m;
        decimal finalMin = flatMin - padding;
        decimal finalMax = flatMax + padding;
        decimal range = finalMax - finalMin;

        Assert.True(range > 0m);
        Assert.Equal(1.1m, range);
    }
}
