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

public class CandlestickChartControl : Control
{
    public static readonly StyledProperty<IList<KlineDto>?> CandlesProperty =
        AvaloniaProperty.Register<CandlestickChartControl, IList<KlineDto>?>(nameof(Candles));

    public static readonly StyledProperty<IList<SweepEventDto>?> SweepEventsProperty =
        AvaloniaProperty.Register<CandlestickChartControl, IList<SweepEventDto>?>(nameof(SweepEvents));

    public static readonly StyledProperty<IList<StructuralLevelDto>?> StructuralLevelsProperty =
        AvaloniaProperty.Register<CandlestickChartControl, IList<StructuralLevelDto>?>(nameof(StructuralLevels));

    public static readonly StyledProperty<string> SymbolProperty =
        AvaloniaProperty.Register<CandlestickChartControl, string>(nameof(Symbol), "BTCUSDT");

    public static readonly StyledProperty<string> TimeframeProperty =
        AvaloniaProperty.Register<CandlestickChartControl, string>(nameof(Timeframe), "1h");

    public IList<KlineDto>? Candles
    {
        get => GetValue(CandlesProperty);
        set => SetValue(CandlesProperty, value);
    }

    public IList<SweepEventDto>? SweepEvents
    {
        get => GetValue(SweepEventsProperty);
        set => SetValue(SweepEventsProperty, value);
    }

    public IList<StructuralLevelDto>? StructuralLevels
    {
        get => GetValue(StructuralLevelsProperty);
        set => SetValue(StructuralLevelsProperty, value);
    }

    public string Symbol
    {
        get => GetValue(SymbolProperty);
        set => SetValue(SymbolProperty, value);
    }

    public string Timeframe
    {
        get => GetValue(TimeframeProperty);
        set => SetValue(TimeframeProperty, value);
    }

    // Interactivity state
    private int _visibleBars = 45;
    private int _panOffset = 0; // 0 = right-aligned (most recent)
    private Point? _crosshairPoint = null;
    private bool _isDragging = false;
    private Point _dragStartPoint;
    private int _dragStartOffset;

    static CandlestickChartControl()
    {
        AffectsRender<CandlestickChartControl>(
            CandlesProperty,
            SweepEventsProperty,
            StructuralLevelsProperty,
            SymbolProperty,
            TimeframeProperty);
    }

    public CandlestickChartControl()
    {
        ClipToBounds = true;
    }

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        var prop = e.GetCurrentPoint(this).Properties;
        if (prop.IsLeftButtonPressed)
        {
            _isDragging = true;
            _dragStartPoint = e.GetPosition(this);
            _dragStartOffset = _panOffset;
            e.Handled = true;
        }
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);
        var currentPoint = e.GetPosition(this);
        _crosshairPoint = currentPoint;

        if (_isDragging && Candles != null && Candles.Count > 0)
        {
            double deltaX = currentPoint.X - _dragStartPoint.X;
            double plotWidth = Math.Max(10, Bounds.Width - 75);
            double barWidth = plotWidth / _visibleBars;
            int barsDelta = (int)Math.Round(deltaX / Math.Max(1.0, barWidth));

            int maxOffset = Math.Max(0, Candles.Count - _visibleBars);
            _panOffset = Math.Clamp(_dragStartOffset + barsDelta, 0, maxOffset);
        }

        InvalidateVisual();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        _isDragging = false;
        InvalidateVisual();
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        _crosshairPoint = null;
        _isDragging = false;
        InvalidateVisual();
    }

    protected override void OnPointerWheelChanged(PointerWheelEventArgs e)
    {
        base.OnPointerWheelChanged(e);
        if (e.Delta.Y > 0)
        {
            // Zoom in (fewer visible bars)
            _visibleBars = Math.Max(15, _visibleBars - 5);
        }
        else if (e.Delta.Y < 0)
        {
            // Zoom out (more visible bars)
            _visibleBars = Math.Min(180, _visibleBars + 5);
        }

        if (Candles != null)
        {
            int maxOffset = Math.Max(0, Candles.Count - _visibleBars);
            _panOffset = Math.Clamp(_panOffset, 0, maxOffset);
        }

        InvalidateVisual();
        e.Handled = true;
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);

        var bounds = Bounds;
        if (bounds.Width <= 20 || bounds.Height <= 20) return;

        // Visual brushes & pens matching Alpha Meridian institutional terminal
        var bgBrush = new SolidColorBrush(Color.Parse("#080A0D"));
        var borderPen = new Pen(new SolidColorBrush(Color.Parse("#242A34")), 1);
        var gridPen = new Pen(new SolidColorBrush(Color.Parse("#161B22")), 1, dashStyle: DashStyle.Dash);
        var bullBrush = new SolidColorBrush(Color.Parse("#00E676"));
        var bearBrush = new SolidColorBrush(Color.Parse("#FF5252"));
        var bullPen = new Pen(bullBrush, 1);
        var bearPen = new Pen(bearBrush, 1);
        var amberPen = new Pen(new SolidColorBrush(Color.Parse("#FF9D00")), 1);
        var textBrush = new SolidColorBrush(Color.Parse("#8B949E"));
        var brightTextBrush = new SolidColorBrush(Color.Parse("#F0F4F8"));
        var levelPen = new Pen(new SolidColorBrush(Color.Parse("#448AFF")), 1, dashStyle: DashStyle.Dash);
        var sweepPen = new Pen(new SolidColorBrush(Color.Parse("#FFD700")), 1.5);
        var crosshairPen = new Pen(new SolidColorBrush(Color.Parse("#545D68")), 1, dashStyle: DashStyle.Dot);

        // Draw background
        context.DrawRectangle(bgBrush, borderPen, new Rect(0, 0, bounds.Width, bounds.Height));

        var candles = Candles;
        if (candles == null || candles.Count == 0)
        {
            var noData = new FormattedText(
                $"WAITING FOR {Symbol} {Timeframe.ToUpperInvariant()} CANDLESTICK STREAM...",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                11,
                new SolidColorBrush(Color.Parse("#FF9D00")));
            context.DrawText(noData, new Point(bounds.Width / 2 - 180, bounds.Height / 2 - 8));
            return;
        }

        // Layout partitions
        double rightAxisWidth = 75;
        double bottomAxisHeight = 22;
        double headerHeight = 20;

        double plotW = Math.Max(10, bounds.Width - rightAxisWidth);
        double totalPlotH = Math.Max(10, bounds.Height - bottomAxisHeight - headerHeight);
        double pricePlotH = totalPlotH * 0.78;
        double volumePlotH = totalPlotH * 0.22;
        double volumeTopY = headerHeight + pricePlotH;

        // Compute visible slice with pan offset
        int totalBars = candles.Count;
        int clampedBars = Math.Min(_visibleBars, totalBars);
        int maxOffset = Math.Max(0, totalBars - clampedBars);
        int curOffset = Math.Clamp(_panOffset, 0, maxOffset);

        int startIndex = Math.Max(0, totalBars - clampedBars - curOffset);
        int endIndex = Math.Min(totalBars, startIndex + clampedBars);
        var slice = new List<KlineDto>();
        for (int i = startIndex; i < endIndex; i++)
        {
            slice.Add(candles[i]);
        }

        if (slice.Count == 0) return;

        // Min/Max bounds for Price
        decimal minPrice = slice.Min(c => c.Low);
        decimal maxPrice = slice.Max(c => c.High);
        if (maxPrice <= minPrice)
        {
            maxPrice = minPrice + 1m;
        }
        decimal pricePadding = (maxPrice - minPrice) * 0.05m;
        minPrice -= pricePadding;
        maxPrice += pricePadding;
        decimal priceRange = maxPrice - minPrice;

        // Max Volume
        decimal maxVolume = slice.Max(c => c.Volume);
        if (maxVolume <= 0) maxVolume = 1m;

        // Grid lines (horizontal price grid)
        int gridDivisions = 5;
        for (int i = 0; i <= gridDivisions; i++)
        {
            double ratio = (double)i / gridDivisions;
            double y = headerHeight + (pricePlotH * ratio);
            context.DrawLine(gridPen, new Point(0, y), new Point(plotW, y));

            decimal p = maxPrice - (priceRange * (decimal)ratio);
            var pText = new FormattedText(
                p >= 100 ? $"{p:F2}" : $"{p:F4}",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                8,
                textBrush);
            context.DrawText(pText, new Point(plotW + 6, y - 5));
        }

        // Structural levels overlay
        if (StructuralLevels != null)
        {
            foreach (var lvl in StructuralLevels)
            {
                if (lvl.Price >= minPrice && lvl.Price <= maxPrice)
                {
                    double y = headerHeight + pricePlotH * (double)((maxPrice - lvl.Price) / priceRange);
                    context.DrawLine(levelPen, new Point(0, y), new Point(plotW, y));

                    var lvlTag = new FormattedText(
                        $"{lvl.LevelType} {lvl.Price:F2}",
                        CultureInfo.InvariantCulture,
                        FlowDirection.LeftToRight,
                        new Typeface("Consolas"),
                        7,
                        new SolidColorBrush(Color.Parse("#448AFF")));
                    context.DrawText(lvlTag, new Point(plotW + 2, y - 10));
                }
            }
        }

        // Render Candles & Volume
        double barSlotWidth = plotW / slice.Count;
        double candleBodyWidth = Math.Max(2.0, barSlotWidth * 0.70);
        double halfBody = candleBodyWidth / 2.0;

        for (int i = 0; i < slice.Count; i++)
        {
            var c = slice[i];
            double centerX = (i * barSlotWidth) + (barSlotWidth / 2.0);

            double openY = headerHeight + pricePlotH * (double)((maxPrice - c.Open) / priceRange);
            double closeY = headerHeight + pricePlotH * (double)((maxPrice - c.Close) / priceRange);
            double highY = headerHeight + pricePlotH * (double)((maxPrice - c.High) / priceRange);
            double lowY = headerHeight + pricePlotH * (double)((maxPrice - c.Low) / priceRange);

            bool isBull = c.Close >= c.Open;
            var candleBrush = isBull ? bullBrush : bearBrush;
            var cPen = isBull ? bullPen : bearPen;

            // Draw wick
            context.DrawLine(cPen, new Point(centerX, highY), new Point(centerX, lowY));

            // Draw body
            double bodyTop = Math.Min(openY, closeY);
            double bodyHeight = Math.Max(1.5, Math.Abs(closeY - openY));
            context.DrawRectangle(candleBrush, cPen, new Rect(centerX - halfBody, bodyTop, candleBodyWidth, bodyHeight));

            // Draw Volume bar at bottom
            double volRatio = (double)(c.Volume / maxVolume);
            double volH = Math.Max(1.0, volumePlotH * volRatio);
            double volY = bounds.Height - bottomAxisHeight - volH;
            var volBrush = isBull ? new SolidColorBrush(Color.FromArgb(120, 0, 230, 118)) : new SolidColorBrush(Color.FromArgb(120, 255, 82, 82));
            context.DrawRectangle(volBrush, null, new Rect(centerX - halfBody, volY, candleBodyWidth, volH));

            // Sweep event markers on this candle
            if (SweepEvents != null)
            {
                foreach (var sw in SweepEvents)
                {
                    if (sw.TimestampMs == c.TimestampMs || (sw.TimestampMs >= c.TimestampMs && sw.TimestampMs < c.TimestampMs + 3600000))
                    {
                        bool isShortSweep = sw.Side.Equals("SHORT", StringComparison.OrdinalIgnoreCase);
                        double markerY = isShortSweep ? highY - 14 : lowY + 4;
                        var markerBrush = isShortSweep ? bearBrush : bullBrush;
                        var markerText = new FormattedText(
                            $"▼ SWP {sw.SweepPrice:F2}",
                            CultureInfo.InvariantCulture,
                            FlowDirection.LeftToRight,
                            new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                            8,
                            markerBrush);
                        context.DrawText(markerText, new Point(centerX - 20, markerY));
                    }
                }
            }

            // Time axis labels every few bars
            int step = Math.Max(1, slice.Count / 6);
            if (i % step == 0 && !string.IsNullOrWhiteSpace(c.DatetimeUtc))
            {
                string timeStr = c.DatetimeUtc.Length >= 16 ? c.DatetimeUtc.Substring(11, 5) : c.DatetimeUtc;
                var tText = new FormattedText(
                    timeStr,
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas"),
                    8,
                    textBrush);
                context.DrawText(tText, new Point(centerX - 12, bounds.Height - bottomAxisHeight + 4));
            }
        }

        // Draw Header / Telemetry Bar
        var lastBar = slice.Last();
        decimal change = lastBar.Close - lastBar.Open;
        decimal changePct = lastBar.Open > 0 ? (change / lastBar.Open) * 100m : 0m;
        string changeSign = change >= 0 ? "+" : "";

        var headerText = new FormattedText(
            $"{Symbol} · {Timeframe.ToUpperInvariant()}   O:{lastBar.Open:F4}  H:{lastBar.High:F4}  L:{lastBar.Low:F4}  C:{lastBar.Close:F4}   CHG:{changeSign}{change:F4} ({changeSign}{changePct:F2}%)   VOL:{lastBar.Volume:N1}",
            CultureInfo.InvariantCulture,
            FlowDirection.LeftToRight,
            new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
            9,
            change >= 0 ? bullBrush : bearBrush);
        context.DrawText(headerText, new Point(10, 4));

        // Draw Interactive Crosshair
        if (_crosshairPoint.HasValue && _crosshairPoint.Value.X < plotW && _crosshairPoint.Value.Y < (bounds.Height - bottomAxisHeight))
        {
            var pt = _crosshairPoint.Value;

            // Horizontal crosshair
            context.DrawLine(crosshairPen, new Point(0, pt.Y), new Point(plotW, pt.Y));

            // Vertical crosshair
            context.DrawLine(crosshairPen, new Point(pt.X, headerHeight), new Point(pt.X, bounds.Height - bottomAxisHeight));

            // Price badge on right axis
            if (pt.Y >= headerHeight && pt.Y <= headerHeight + pricePlotH)
            {
                double ratio = (pt.Y - headerHeight) / pricePlotH;
                decimal crosshairPrice = maxPrice - (priceRange * (decimal)ratio);
                var priceBadgeRect = new Rect(plotW + 2, pt.Y - 8, rightAxisWidth - 4, 16);
                context.DrawRectangle(new SolidColorBrush(Color.Parse("#FF9D00")), null, priceBadgeRect);
                var pBadgeText = new FormattedText(
                    crosshairPrice >= 100 ? $"{crosshairPrice:F2}" : $"{crosshairPrice:F4}",
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                    8,
                    new SolidColorBrush(Color.Parse("#000000")));
                context.DrawText(pBadgeText, new Point(plotW + 6, pt.Y - 6));
            }
        }
    }
}
