using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using SignalGenerator.Desktop.Models;

namespace SignalGenerator.Desktop.Controls;

public class MarketDepthChartControl : Control
{
    public static readonly StyledProperty<IList<OrderBookBucketDto>?> BidsProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, IList<OrderBookBucketDto>?>(nameof(Bids));

    public static readonly StyledProperty<IList<OrderBookBucketDto>?> AsksProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, IList<OrderBookBucketDto>?>(nameof(Asks));

    public static readonly StyledProperty<decimal?> MidPriceProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, decimal?>(nameof(MidPrice));

    public static readonly StyledProperty<decimal?> SpreadBpsProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, decimal?>(nameof(SpreadBps));

    public static readonly StyledProperty<double?> AgeSecondsProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, double?>(nameof(AgeSeconds));

    public static readonly StyledProperty<string> StatusProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, string>(nameof(Status), "AVAILABLE");

    public static readonly StyledProperty<string> VenueSourceProperty =
        AvaloniaProperty.Register<MarketDepthChartControl, string>(nameof(VenueSource), "Combined L2 (AsterDEX, Binance, Bybit)");

    public IList<OrderBookBucketDto>? Bids
    {
        get => GetValue(BidsProperty);
        set => SetValue(BidsProperty, value);
    }

    public IList<OrderBookBucketDto>? Asks
    {
        get => GetValue(AsksProperty);
        set => SetValue(AsksProperty, value);
    }

    public decimal? MidPrice
    {
        get => GetValue(MidPriceProperty);
        set => SetValue(MidPriceProperty, value);
    }

    public decimal? SpreadBps
    {
        get => GetValue(SpreadBpsProperty);
        set => SetValue(SpreadBpsProperty, value);
    }

    public double? AgeSeconds
    {
        get => GetValue(AgeSecondsProperty);
        set => SetValue(AgeSecondsProperty, value);
    }

    public string Status
    {
        get => GetValue(StatusProperty);
        set => SetValue(StatusProperty, value);
    }

    public string VenueSource
    {
        get => GetValue(VenueSourceProperty);
        set => SetValue(VenueSourceProperty, value);
    }

    private Point? _crosshairPoint = null;

    static MarketDepthChartControl()
    {
        AffectsRender<MarketDepthChartControl>(
            BidsProperty,
            AsksProperty,
            MidPriceProperty,
            SpreadBpsProperty,
            AgeSecondsProperty,
            StatusProperty,
            VenueSourceProperty);
    }

    public MarketDepthChartControl()
    {
        ClipToBounds = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        _crosshairPoint = e.GetPosition(this);
        InvalidateVisual();
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        _crosshairPoint = null;
        InvalidateVisual();
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);

        var bounds = Bounds;
        if (bounds.Width <= 20 || bounds.Height <= 20) return;

        var bgBrush = new SolidColorBrush(Color.Parse("#080A0D"));
        var borderPen = new Pen(new SolidColorBrush(Color.Parse("#242A34")), 1);
        var gridPen = new Pen(new SolidColorBrush(Color.Parse("#161B22")), 1, dashStyle: DashStyle.Dash);
        var textBrush = new SolidColorBrush(Color.Parse("#8B949E"));
        var brightTextBrush = new SolidColorBrush(Color.Parse("#F0F4F8"));
        var bidPen = new Pen(new SolidColorBrush(Color.Parse("#00E676")), 2);
        var askPen = new Pen(new SolidColorBrush(Color.Parse("#FF5252")), 2);
        var bidFill = new SolidColorBrush(Color.FromArgb(45, 0, 230, 118));
        var askFill = new SolidColorBrush(Color.FromArgb(45, 255, 82, 82));
        var amberPen = new Pen(new SolidColorBrush(Color.Parse("#FF9D00")), 1, dashStyle: DashStyle.Dash);
        var crosshairPen = new Pen(new SolidColorBrush(Color.Parse("#545D68")), 1, dashStyle: DashStyle.Dot);

        // Draw background
        context.DrawRectangle(bgBrush, borderPen, new Rect(0, 0, bounds.Width, bounds.Height));

        // Header info bar
        double headerHeight = 22;
        double bottomAxisHeight = 20;
        double rightAxisWidth = 70;
        double plotW = Math.Max(10, bounds.Width - rightAxisWidth);
        double plotH = Math.Max(10, bounds.Height - headerHeight - bottomAxisHeight);
        double plotBottomY = headerHeight + plotH;

        var bidsList = Bids?.Where(b => b.BucketPrice > 0 && b.TotalNotionalUsdt > 0).ToList() ?? new List<OrderBookBucketDto>();
        var asksList = Asks?.Where(a => a.BucketPrice > 0 && a.TotalNotionalUsdt > 0).ToList() ?? new List<OrderBookBucketDto>();

        if (bidsList.Count == 0 && asksList.Count == 0)
        {
            var noData = new FormattedText(
                "CUMULATIVE MARKET DEPTH UNAVAILABLE // Awaiting L2 Book Stream",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                10,
                new SolidColorBrush(Color.Parse("#FF9D00")));
            context.DrawText(noData, new Point(bounds.Width / 2 - 180, bounds.Height / 2 - 6));
            return;
        }

        // Sort bids descending (highest bid closest to mid), asks ascending (lowest ask closest to mid)
        var sortedBids = bidsList.OrderByDescending(b => b.BucketPrice).ToList();
        var sortedAsks = asksList.OrderBy(a => a.BucketPrice).ToList();

        // Calculate cumulative notional series
        var cumBids = new List<(decimal price, decimal cumNotional)>();
        decimal runningBid = 0m;
        foreach (var b in sortedBids)
        {
            runningBid += b.TotalNotionalUsdt;
            cumBids.Add((b.BucketPrice, runningBid));
        }

        var cumAsks = new List<(decimal price, decimal cumNotional)>();
        decimal runningAsk = 0m;
        foreach (var a in sortedAsks)
        {
            runningAsk += a.TotalNotionalUsdt;
            cumAsks.Add((a.BucketPrice, runningAsk));
        }

        decimal maxCumDepth = Math.Max(runningBid, runningAsk);
        if (maxCumDepth <= 0m) maxCumDepth = 1000m;
        maxCumDepth *= 1.05m; // 5% ceiling padding

        decimal minPrice = cumBids.Count > 0 ? cumBids.Last().price : (MidPrice ?? 100m) * 0.99m;
        decimal maxPrice = cumAsks.Count > 0 ? cumAsks.Last().price : (MidPrice ?? 100m) * 1.01m;
        if (maxPrice <= minPrice) maxPrice = minPrice + 1m;
        decimal priceRange = maxPrice - minPrice;

        decimal mid = MidPrice ?? ((minPrice + maxPrice) / 2m);
        double midX = (double)((mid - minPrice) / priceRange) * plotW;
        midX = Math.Clamp(midX, 5.0, plotW - 5.0);

        // Draw horizontal grid lines (depth)
        int depthDivisions = 4;
        for (int i = 0; i <= depthDivisions; i++)
        {
            double ratio = (double)i / depthDivisions;
            double y = headerHeight + (plotH * (1.0 - ratio));
            context.DrawLine(gridPen, new Point(0, y), new Point(plotW, y));

            decimal depthVal = maxCumDepth * (decimal)ratio;
            string depthText = depthVal >= 1_000_000m ? $"${depthVal / 1_000_000m:F2}M" : $"${depthVal / 1_000m:F0}K";
            var dFt = new FormattedText(
                depthText,
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                8,
                textBrush);
            context.DrawText(dFt, new Point(plotW + 5, y - 5));
        }

        // Draw vertical price grid lines
        int priceDivisions = 6;
        for (int i = 0; i <= priceDivisions; i++)
        {
            double ratio = (double)i / priceDivisions;
            double x = plotW * ratio;
            context.DrawLine(gridPen, new Point(x, headerHeight), new Point(x, plotBottomY));

            decimal pVal = minPrice + (priceRange * (decimal)ratio);
            var pFt = new FormattedText(
                pVal >= 100m ? $"{pVal:F1}" : $"{pVal:F4}",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                8,
                textBrush);
            context.DrawText(pFt, new Point(Math.Max(2, x - 15), plotBottomY + 4));
        }

        // Draw Mid-Price Center Line
        context.DrawLine(amberPen, new Point(midX, headerHeight), new Point(midX, plotBottomY));

        // Render Bids Cumulative Area & Step Curve
        if (cumBids.Count > 0)
        {
            var bidGeom = new StreamGeometry();
            using (var ctx = bidGeom.Open())
            {
                // Start at bottom of mid price
                ctx.BeginFigure(new Point(midX, plotBottomY), isFilled: true);

                // Step curve from highest bid down to lowest bid
                double prevX = midX;
                double prevY = plotBottomY;

                foreach (var (p, cum) in cumBids)
                {
                    double x = (double)((p - minPrice) / priceRange) * plotW;
                    double y = plotBottomY - (double)(cum / maxCumDepth) * plotH;

                    // Step
                    ctx.LineTo(new Point(prevX, y));
                    ctx.LineTo(new Point(x, y));
                    prevX = x;
                    prevY = y;
                }

                ctx.LineTo(new Point(prevX, plotBottomY));
                ctx.EndFigure(isClosed: true);
            }
            context.DrawGeometry(bidFill, bidPen, bidGeom);
        }

        // Render Asks Cumulative Area & Step Curve
        if (cumAsks.Count > 0)
        {
            var askGeom = new StreamGeometry();
            using (var ctx = askGeom.Open())
            {
                ctx.BeginFigure(new Point(midX, plotBottomY), isFilled: true);

                double prevX = midX;
                double prevY = plotBottomY;

                foreach (var (p, cum) in cumAsks)
                {
                    double x = (double)((p - minPrice) / priceRange) * plotW;
                    double y = plotBottomY - (double)(cum / maxCumDepth) * plotH;

                    // Step
                    ctx.LineTo(new Point(prevX, y));
                    ctx.LineTo(new Point(x, y));
                    prevX = x;
                    prevY = y;
                }

                ctx.LineTo(new Point(prevX, plotBottomY));
                ctx.EndFigure(isClosed: true);
            }
            context.DrawGeometry(askFill, askPen, askGeom);
        }

        // Draw Best Bid and Best Ask markers
        if (sortedBids.Count > 0)
        {
            decimal bestBidPx = sortedBids[0].BucketPrice;
            double bX = (double)((bestBidPx - minPrice) / priceRange) * plotW;
            context.DrawLine(bidPen, new Point(bX, headerHeight), new Point(bX, headerHeight + 8));

            var bestBidFt = new FormattedText(
                $"BEST BID ${bestBidPx:F2}",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                8,
                new SolidColorBrush(Color.Parse("#00E676")));
            context.DrawText(bestBidFt, new Point(Math.Max(6, bX - 90), headerHeight + 6));
        }

        if (sortedAsks.Count > 0)
        {
            decimal bestAskPx = sortedAsks[0].BucketPrice;
            double aX = (double)((bestAskPx - minPrice) / priceRange) * plotW;
            context.DrawLine(askPen, new Point(aX, headerHeight), new Point(aX, headerHeight + 8));

            var bestAskFt = new FormattedText(
                $"BEST ASK ${bestAskPx:F2}",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                8,
                new SolidColorBrush(Color.Parse("#FF5252")));
            context.DrawText(bestAskFt, new Point(Math.Min(plotW - 90, aX + 8), headerHeight + 6));
        }

        // Draw Header Telemetry Text
        string spreadStr = SpreadBps.HasValue ? $"{SpreadBps.Value:F2} bps" : "--";
        string ageStr = AgeSeconds.HasValue ? $"{AgeSeconds.Value:F1}s" : "0.0s";
        string headerStr = $"■ CUMULATIVE MARKET DEPTH // {VenueSource}   MID: ${mid:F2}   SPREAD: {spreadStr}   AGE: {ageStr}   STATUS: {Status}";
        var hFt = new FormattedText(
            headerStr,
            CultureInfo.InvariantCulture,
            FlowDirection.LeftToRight,
            new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
            9,
            new SolidColorBrush(Color.Parse("#FF9D00")));
        context.DrawText(hFt, new Point(6, 4));

        // Draw Interactive Crosshair & Tooltip
        if (_crosshairPoint.HasValue && _crosshairPoint.Value.X <= plotW && _crosshairPoint.Value.Y >= headerHeight && _crosshairPoint.Value.Y <= plotBottomY)
        {
            var pt = _crosshairPoint.Value;
            context.DrawLine(crosshairPen, new Point(0, pt.Y), new Point(plotW, pt.Y));
            context.DrawLine(crosshairPen, new Point(pt.X, headerHeight), new Point(pt.X, plotBottomY));

            decimal crosshairPrice = minPrice + (priceRange * (decimal)(pt.X / plotW));
            decimal crosshairDepth = maxCumDepth * (decimal)((plotBottomY - pt.Y) / plotH);

            string badgeStr = crosshairDepth >= 1_000_000m
                ? $"${crosshairPrice:F2} | Depth: ${crosshairDepth / 1_000_000m:F2}M"
                : $"${crosshairPrice:F2} | Depth: ${crosshairDepth / 1_000m:F0}K";

            var badgeFt = new FormattedText(
                badgeStr,
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                8,
                brightTextBrush);

            double badgeX = Math.Clamp(pt.X - 50, 4, plotW - 140);
            double badgeY = Math.Max(headerHeight + 2, pt.Y - 18);
            context.DrawRectangle(new SolidColorBrush(Color.Parse("#161B22")), borderPen, new Rect(badgeX - 4, badgeY - 2, badgeFt.Width + 8, badgeFt.Height + 4));
            context.DrawText(badgeFt, new Point(badgeX, badgeY));
        }
    }
}
