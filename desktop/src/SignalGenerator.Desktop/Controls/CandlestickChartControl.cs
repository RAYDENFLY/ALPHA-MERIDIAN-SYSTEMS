using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Media;
using SignalGenerator.Desktop.Indicators;
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

    public static readonly StyledProperty<IList<OrderBookWallDto>?> WallsProperty =
        AvaloniaProperty.Register<CandlestickChartControl, IList<OrderBookWallDto>?>(nameof(Walls));

    public static readonly StyledProperty<string> SymbolProperty =
        AvaloniaProperty.Register<CandlestickChartControl, string>(nameof(Symbol), "BTCUSDT");

    public static readonly StyledProperty<string> TimeframeProperty =
        AvaloniaProperty.Register<CandlestickChartControl, string>(nameof(Timeframe), "1h");

    public static readonly StyledProperty<bool> ShowEma20Property =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowEma20), true);

    public static readonly StyledProperty<bool> ShowEma50Property =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowEma50), true);

    public static readonly StyledProperty<bool> ShowEma100Property =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowEma100), true);

    public static readonly StyledProperty<bool> ShowEma200Property =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowEma200), true);

    public static readonly StyledProperty<bool> ShowStochasticProperty =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowStochastic), true);

    public static readonly StyledProperty<bool> ShowBidWallsProperty =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowBidWalls), true);

    public static readonly StyledProperty<bool> ShowAskWallsProperty =
        AvaloniaProperty.Register<CandlestickChartControl, bool>(nameof(ShowAskWalls), true);

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

    public IList<OrderBookWallDto>? Walls
    {
        get => GetValue(WallsProperty);
        set => SetValue(WallsProperty, value);
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

    public bool ShowEma20
    {
        get => GetValue(ShowEma20Property);
        set => SetValue(ShowEma20Property, value);
    }

    public bool ShowEma50
    {
        get => GetValue(ShowEma50Property);
        set => SetValue(ShowEma50Property, value);
    }

    public bool ShowEma100
    {
        get => GetValue(ShowEma100Property);
        set => SetValue(ShowEma100Property, value);
    }

    public bool ShowEma200
    {
        get => GetValue(ShowEma200Property);
        set => SetValue(ShowEma200Property, value);
    }

    public bool ShowStochastic
    {
        get => GetValue(ShowStochasticProperty);
        set => SetValue(ShowStochasticProperty, value);
    }

    public bool ShowBidWalls
    {
        get => GetValue(ShowBidWallsProperty);
        set => SetValue(ShowBidWallsProperty, value);
    }

    public bool ShowAskWalls
    {
        get => GetValue(ShowAskWallsProperty);
        set => SetValue(ShowAskWallsProperty, value);
    }

    // Navigation and viewport state
    private int _visibleBars = 50;
    private int _panOffset = 0; // 0 = right-aligned with latest bar
    private Point? _crosshairPoint = null;
    private bool _isDragging = false;
    private Point _dragStartPoint;
    private int _dragStartOffset;

    // Indicator caching
    private IList<KlineDto>? _cachedCandlesRef = null;
    private decimal?[]? _cachedEma20 = null;
    private decimal?[]? _cachedEma50 = null;
    private decimal?[]? _cachedEma100 = null;
    private decimal?[]? _cachedEma200 = null;
    private StochasticResult? _cachedStochastic = null;

    static CandlestickChartControl()
    {
        AffectsRender<CandlestickChartControl>(
            CandlesProperty,
            SweepEventsProperty,
            StructuralLevelsProperty,
            WallsProperty,
            SymbolProperty,
            TimeframeProperty,
            ShowEma20Property,
            ShowEma50Property,
            ShowEma100Property,
            ShowEma200Property,
            ShowStochasticProperty,
            ShowBidWallsProperty,
            ShowAskWallsProperty);
    }

    public CandlestickChartControl()
    {
        ClipToBounds = true;
    }

    public void ResetView()
    {
        _visibleBars = 50;
        _panOffset = 0;
        InvalidateVisual();
    }

    public void Autoscale()
    {
        InvalidateVisual();
    }

    public void GoToLatest()
    {
        _panOffset = 0;
        InvalidateVisual();
    }

    protected override void OnPointerPressed(PointerPressedEventArgs e)
    {
        base.OnPointerPressed(e);
        var prop = e.GetCurrentPoint(this).Properties;
        if (prop.IsLeftButtonPressed)
        {
            e.Pointer.Capture(this);
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
            double barWidth = plotWidth / Math.Max(1, _visibleBars);
            int barsDelta = (int)Math.Round(deltaX / Math.Max(1.0, barWidth));

            int maxOffset = Math.Max(0, Candles.Count - _visibleBars);
            _panOffset = Math.Clamp(_dragStartOffset + barsDelta, 0, maxOffset);
        }

        InvalidateVisual();
    }

    protected override void OnPointerReleased(PointerReleasedEventArgs e)
    {
        base.OnPointerReleased(e);
        if (_isDragging)
        {
            _isDragging = false;
            e.Pointer.Capture(null);
            InvalidateVisual();
        }
    }

    protected override void OnPointerCaptureLost(PointerCaptureLostEventArgs e)
    {
        base.OnPointerCaptureLost(e);
        _isDragging = false;
        InvalidateVisual();
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        _crosshairPoint = null;
        if (!_isDragging)
        {
            InvalidateVisual();
        }
    }

    protected override void OnPointerWheelChanged(PointerWheelEventArgs e)
    {
        base.OnPointerWheelChanged(e);
        var candles = Candles;
        if (candles == null || candles.Count == 0) return;

        double plotWidth = Math.Max(10, Bounds.Width - 75);
        var pos = e.GetPosition(this);

        // Calculate cursor anchor ratio (0.0 at left, 1.0 at right)
        double r = Math.Clamp(pos.X / Math.Max(10.0, plotWidth), 0.0, 1.0);

        int totalBars = candles.Count;
        int clampedBars = Math.Min(_visibleBars, totalBars);
        int maxOffset = Math.Max(0, totalBars - clampedBars);
        int curOffset = Math.Clamp(_panOffset, 0, maxOffset);
        int currentStartIndex = Math.Max(0, totalBars - clampedBars - curOffset);

        // Candle index under the cursor
        double anchorIdx = currentStartIndex + (r * clampedBars);

        // Zoom delta proportional to visible bars
        int zoomStep = Math.Max(2, clampedBars / 8);
        int newVisible;
        if (e.Delta.Y > 0)
        {
            // Zoom in
            newVisible = Math.Max(12, clampedBars - zoomStep);
        }
        else
        {
            // Zoom out
            newVisible = Math.Min(totalBars, clampedBars + zoomStep);
        }

        // Keep anchorIdx at the same relative position r
        double newStartIndex = anchorIdx - (r * newVisible);
        int newPanOffset = (int)Math.Round(totalBars - newVisible - newStartIndex);
        int newMaxOffset = Math.Max(0, totalBars - newVisible);

        _visibleBars = newVisible;
        _panOffset = Math.Clamp(newPanOffset, 0, newMaxOffset);

        InvalidateVisual();
        e.Handled = true;
    }

    private void EnsureIndicatorsCalculated(IList<KlineDto> candles)
    {
        if (ReferenceEquals(_cachedCandlesRef, candles) && _cachedEma20 != null && _cachedStochastic != null)
        {
            return;
        }

        var readOnlyCandles = candles as IReadOnlyList<KlineDto> ?? candles.ToList();
        _cachedCandlesRef = candles;
        _cachedEma20 = TechnicalIndicators.ComputeEma(readOnlyCandles, 20);
        _cachedEma50 = TechnicalIndicators.ComputeEma(readOnlyCandles, 50);
        _cachedEma100 = TechnicalIndicators.ComputeEma(readOnlyCandles, 100);
        _cachedEma200 = TechnicalIndicators.ComputeEma(readOnlyCandles, 200);
        _cachedStochastic = TechnicalIndicators.ComputeStochastic(readOnlyCandles);
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);

        var bounds = Bounds;
        if (bounds.Width <= 20 || bounds.Height <= 20) return;

        // Color palette matching institutional Alpha Meridian Systems
        var bgBrush = new SolidColorBrush(Color.Parse("#080A0D"));
        var borderPen = new Pen(new SolidColorBrush(Color.Parse("#242A34")), 1);
        var gridPen = new Pen(new SolidColorBrush(Color.Parse("#161B22")), 1, dashStyle: DashStyle.Dash);
        var bullBrush = new SolidColorBrush(Color.Parse("#00E676"));
        var bearBrush = new SolidColorBrush(Color.Parse("#FF5252"));
        var bullPen = new Pen(bullBrush, 1);
        var bearPen = new Pen(bearBrush, 1);
        var textBrush = new SolidColorBrush(Color.Parse("#8B949E"));
        var brightTextBrush = new SolidColorBrush(Color.Parse("#F0F4F8"));
        var levelPen = new Pen(new SolidColorBrush(Color.Parse("#448AFF")), 1, dashStyle: DashStyle.Dash);
        var crosshairPen = new Pen(new SolidColorBrush(Color.Parse("#545D68")), 1, dashStyle: DashStyle.Dot);

        // EMA Pens
        var ema20Pen = new Pen(new SolidColorBrush(Color.Parse("#FFD700")), 1.2); // Gold
        var ema50Pen = new Pen(new SolidColorBrush(Color.Parse("#FF9100")), 1.2); // Orange
        var ema100Pen = new Pen(new SolidColorBrush(Color.Parse("#00E5FF")), 1.2); // Cyan
        var ema200Pen = new Pen(new SolidColorBrush(Color.Parse("#E040FB")), 1.2); // Magenta

        // Stochastic Pens
        var stochKPen = new Pen(new SolidColorBrush(Color.Parse("#00E5FF")), 1.2); // Cyan
        var stochDPen = new Pen(new SolidColorBrush(Color.Parse("#FF9100")), 1.2); // Orange

        // Background
        context.DrawRectangle(bgBrush, borderPen, new Rect(0, 0, bounds.Width, bounds.Height));

        var candles = Candles;
        if (candles == null || candles.Count == 0)
        {
            var noData = new FormattedText(
                $"WAITING FOR {Symbol} {Timeframe.ToUpperInvariant()} CANDLESTICK STREAM...",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                11,
                new SolidColorBrush(Color.Parse("#FF9D00")));
            context.DrawText(noData, new Point(bounds.Width / 2 - 180, bounds.Height / 2 - 8));
            return;
        }

        EnsureIndicatorsCalculated(candles);

        // Vertical space partitioning
        double rightAxisWidth = 75;
        double bottomAxisHeight = 20;
        double headerHeight = 32;

        bool hasStoch = ShowStochastic;
        double stochPaneH = hasStoch ? Math.Clamp(bounds.Height * 0.22, 65.0, 95.0) : 0.0;
        double mainAreaH = Math.Max(20, bounds.Height - headerHeight - bottomAxisHeight - stochPaneH);

        double pricePlotH = mainAreaH * 0.80;
        double volumePlotH = mainAreaH * 0.20;
        double volumeTopY = headerHeight + pricePlotH;
        double stochTopY = headerHeight + mainAreaH;
        double plotW = Math.Max(10, bounds.Width - rightAxisWidth);

        // Header Background Strip
        context.DrawRectangle(new SolidColorBrush(Color.Parse("#0B0E12")), borderPen, new Rect(0, 0, bounds.Width, headerHeight));

        // Compute visible slice with pan offset
        int totalBars = candles.Count;
        int clampedBars = Math.Min(_visibleBars, totalBars);
        int maxOffset = Math.Max(0, totalBars - clampedBars);
        int curOffset = Math.Clamp(_panOffset, 0, maxOffset);

        int startIndex = Math.Max(0, totalBars - clampedBars - curOffset);
        int endIndex = Math.Min(totalBars, startIndex + clampedBars);
        int visibleCount = endIndex - startIndex;

        if (visibleCount <= 0) return;

        var slice = new List<KlineDto>(visibleCount);
        for (int i = startIndex; i < endIndex; i++)
        {
            slice.Add(candles[i]);
        }

        // Min/Max bounds for Price
        decimal minPrice = slice.Min(c => c.Low);
        decimal maxPrice = slice.Max(c => c.High);
        if (maxPrice <= minPrice) maxPrice = minPrice + 1m;
        decimal pricePadding = (maxPrice - minPrice) * 0.05m;
        minPrice -= pricePadding;
        maxPrice += pricePadding;
        decimal priceRange = maxPrice - minPrice;

        // Max Volume
        decimal maxVolume = slice.Max(c => c.Volume);
        if (maxVolume <= 0) maxVolume = 1m;

        // Horizontal price grid lines
        int gridDivisions = 5;
        for (int i = 0; i <= gridDivisions; i++)
        {
            double ratio = (double)i / gridDivisions;
            double y = headerHeight + (pricePlotH * ratio);
            context.DrawLine(gridPen, new Point(0, y), new Point(plotW, y));

            decimal p = maxPrice - (priceRange * (decimal)ratio);
            var pText = new FormattedText(
                p >= 100m ? $"{p:F2}" : $"{p:F4}",
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                8,
                textBrush);
            context.DrawText(pText, new Point(plotW + 5, y - 5));
        }

        // Structural levels overlay (filter to active/HTF or recent to prevent crowding)
        if (StructuralLevels != null)
        {
            var activeLvls = StructuralLevels.Where(l => l.Status == "ACTIVE" || l.IsHtf).ToList();
            if (activeLvls.Count == 0) activeLvls = StructuralLevels.TakeLast(6).ToList();

            foreach (var lvl in activeLvls)
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
                    context.DrawRectangle(new SolidColorBrush(Color.FromArgb(180, 8, 10, 13)), null, new Rect(6, y - 8, lvlTag.Width + 4, 10));
                    context.DrawText(lvlTag, new Point(8, y - 8));
                }
            }
        }

        // LIQUIDITY WALL OVERLAYS ON MAIN PRICE CHART
        if (Walls != null)
        {
            double lastLabelY = -999.0;
            foreach (var w in Walls.OrderByDescending(x => x.NotionalUsdt))
            {
                bool isBid = w.Side.Equals("BID", StringComparison.OrdinalIgnoreCase);
                if (isBid && !ShowBidWalls) continue;
                if (!isBid && !ShowAskWalls) continue;

                if (w.Price >= minPrice && w.Price <= maxPrice)
                {
                    double y = headerHeight + pricePlotH * (double)((maxPrice - w.Price) / priceRange);
                    var wallColor = isBid ? Color.Parse("#00E676") : Color.Parse("#FF5252");
                    bool isSignificant = (w.Multiple ?? 1m) >= 3.0m || w.IsPersistent || (w.ConfirmationTier?.Contains("CONFIRMED") ?? false);

                    // Translucent zone for significant walls
                    if (isSignificant)
                    {
                        var zoneBrush = new SolidColorBrush(Color.FromArgb(20, wallColor.R, wallColor.G, wallColor.B));
                        context.DrawRectangle(zoneBrush, null, new Rect(0, y - 4, plotW, 8));
                    }

                    var wallPen = new Pen(new SolidColorBrush(wallColor), isSignificant ? 1.8 : 1.0, dashStyle: DashStyle.Dash);
                    context.DrawLine(wallPen, new Point(0, y), new Point(plotW, y));

                    // Label placement with vertical collision dodging
                    if (Math.Abs(y - lastLabelY) > 14.0)
                    {
                        string notionalStr = w.NotionalUsdt >= 1_000_000m 
                            ? $"${w.NotionalUsdt / 1_000_000m:F2}M" 
                            : $"${w.NotionalUsdt / 1_000m:F0}K";

                        string distStr = w.DistanceBps.HasValue ? $"{w.DistanceBps.Value:F1}bps" : "";
                        string labelStr = $"▶ {(isBid ? "BID" : "ASK")} WALL ${w.Price:F2} ({notionalStr} {distStr})";

                        var wallFt = new FormattedText(
                            labelStr,
                            CultureInfo.InvariantCulture,
                            FlowDirection.LeftToRight,
                            new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                            7,
                            new SolidColorBrush(wallColor));

                        context.DrawRectangle(new SolidColorBrush(Color.FromArgb(180, 8, 10, 13)), null, new Rect(plotW - wallFt.Width - 12, y - 6, wallFt.Width + 8, 12));
                        context.DrawText(wallFt, new Point(plotW - wallFt.Width - 8, y - 5));
                        lastLabelY = y;
                    }
                }
            }
        }

        // Render Candles & Volume
        double barSlotWidth = plotW / visibleCount;
        double candleBodyWidth = Math.Max(2.0, barSlotWidth * 0.70);
        double halfBody = candleBodyWidth / 2.0;

        for (int i = 0; i < visibleCount; i++)
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

            // Wick
            context.DrawLine(cPen, new Point(centerX, highY), new Point(centerX, lowY));

            // Body
            double bodyTop = Math.Min(openY, closeY);
            double bodyHeight = Math.Max(1.5, Math.Abs(closeY - openY));
            context.DrawRectangle(candleBrush, cPen, new Rect(centerX - halfBody, bodyTop, candleBodyWidth, bodyHeight));

            // Volume bar
            double volRatio = (double)(c.Volume / maxVolume);
            double volH = Math.Max(1.0, volumePlotH * volRatio);
            double volY = headerHeight + mainAreaH - volH;
            var volBrush = isBull 
                ? new SolidColorBrush(Color.FromArgb(100, 0, 230, 118)) 
                : new SolidColorBrush(Color.FromArgb(100, 255, 82, 82));
            context.DrawRectangle(volBrush, null, new Rect(centerX - halfBody, volY, candleBodyWidth, volH));

            // Sweep event markers
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

            // Time axis labels
            int step = Math.Max(1, visibleCount / 6);
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

        // RENDER EMA OVERLAYS (20, 50, 100, 200)
        void DrawEmaLine(decimal?[]? emaSeries, Pen pen)
        {
            if (emaSeries == null || emaSeries.Length < totalBars) return;

            Point? lastPt = null;
            for (int i = 0; i < visibleCount; i++)
            {
                int fullIdx = startIndex + i;
                var val = emaSeries[fullIdx];
                if (!val.HasValue)
                {
                    lastPt = null;
                    continue;
                }

                double centerX = (i * barSlotWidth) + (barSlotWidth / 2.0);
                double y = headerHeight + pricePlotH * (double)((maxPrice - val.Value) / priceRange);
                var currentPt = new Point(centerX, y);

                if (lastPt.HasValue)
                {
                    context.DrawLine(pen, lastPt.Value, currentPt);
                }
                lastPt = currentPt;
            }
        }

        if (ShowEma20) DrawEmaLine(_cachedEma20, ema20Pen);
        if (ShowEma50) DrawEmaLine(_cachedEma50, ema50Pen);
        if (ShowEma100) DrawEmaLine(_cachedEma100, ema100Pen);
        if (ShowEma200) DrawEmaLine(_cachedEma200, ema200Pen);

        // RENDER STOCHASTIC 5,3,3 PANE
        if (hasStoch && _cachedStochastic != null)
        {
            // Separator line
            context.DrawLine(borderPen, new Point(0, stochTopY), new Point(bounds.Width, stochTopY));

            // Stochastic guide levels (80, 50, 20)
            double stochPlotH = stochPaneH - 12.0;
            double stochPlotTop = stochTopY + 8.0;

            double[] guideLevels = { 80.0, 50.0, 20.0 };
            foreach (var lvl in guideLevels)
            {
                double y = stochPlotTop + (stochPlotH * (1.0 - (lvl / 100.0)));
                var gPen = (lvl == 50.0) ? gridPen : new Pen(new SolidColorBrush(Color.Parse("#242A34")), 1, dashStyle: DashStyle.Dash);
                context.DrawLine(gPen, new Point(0, y), new Point(plotW, y));

                var lText = new FormattedText(
                    lvl.ToString("F0"),
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas"),
                    7,
                    textBrush);
                context.DrawText(lText, new Point(plotW + 5, y - 4));
            }

            // Draw %K and %D lines
            void DrawStochCurve(decimal?[] series, Pen pen)
            {
                Point? prevPt = null;
                for (int i = 0; i < visibleCount; i++)
                {
                    int fullIdx = startIndex + i;
                    if (fullIdx >= series.Length) break;
                    var val = series[fullIdx];
                    if (!val.HasValue)
                    {
                        prevPt = null;
                        continue;
                    }

                    double centerX = (i * barSlotWidth) + (barSlotWidth / 2.0);
                    double y = stochPlotTop + (stochPlotH * (1.0 - (double)(val.Value / 100m)));
                    var curPt = new Point(centerX, y);

                    if (prevPt.HasValue)
                    {
                        context.DrawLine(pen, prevPt.Value, curPt);
                    }
                    prevPt = curPt;
                }
            }

            DrawStochCurve(_cachedStochastic.SmoothK, stochKPen);
            DrawStochCurve(_cachedStochastic.PercentD, stochDPen);
        }

        // Active Bar / Crosshair Hover Resolution
        int activeBarIdx = visibleCount - 1;
        if (_crosshairPoint.HasValue && _crosshairPoint.Value.X < plotW && _crosshairPoint.Value.X >= 0)
        {
            int hoveredSlot = (int)(_crosshairPoint.Value.X / barSlotWidth);
            if (hoveredSlot >= 0 && hoveredSlot < visibleCount)
            {
                activeBarIdx = hoveredSlot;
            }
        }

        var activeBar = slice[activeBarIdx];
        int activeFullIdx = startIndex + activeBarIdx;
        decimal change = activeBar.Close - activeBar.Open;
        decimal changePct = activeBar.Open > 0 ? (change / activeBar.Open) * 100m : 0m;
        string changeSign = change >= 0 ? "+" : "";

        // Legend values
        string emaStr = "";
        if (ShowEma20 && _cachedEma20 != null && activeFullIdx < _cachedEma20.Length && _cachedEma20[activeFullIdx].HasValue)
            emaStr += $" EMA20:{_cachedEma20[activeFullIdx]!.Value:F2}";
        if (ShowEma50 && _cachedEma50 != null && activeFullIdx < _cachedEma50.Length && _cachedEma50[activeFullIdx].HasValue)
            emaStr += $" EMA50:{_cachedEma50[activeFullIdx]!.Value:F2}";
        if (ShowEma100 && _cachedEma100 != null && activeFullIdx < _cachedEma100.Length && _cachedEma100[activeFullIdx].HasValue)
            emaStr += $" EMA100:{_cachedEma100[activeFullIdx]!.Value:F2}";
        if (ShowEma200 && _cachedEma200 != null && activeFullIdx < _cachedEma200.Length && _cachedEma200[activeFullIdx].HasValue)
            emaStr += $" EMA200:{_cachedEma200[activeFullIdx]!.Value:F2}";

        string stochStr = "";
        if (hasStoch && _cachedStochastic != null && activeFullIdx < _cachedStochastic.SmoothK.Length)
        {
            var kVal = _cachedStochastic.SmoothK[activeFullIdx];
            var dVal = _cachedStochastic.PercentD[activeFullIdx];
            stochStr = $" STOCH(5,3,3) %K:{(kVal.HasValue ? kVal.Value.ToString("F1") : "--")} %D:{(dVal.HasValue ? dVal.Value.ToString("F1") : "--")}";
        }

        // Draw Header Telemetry Bar (Row 1: OHLCV & Volume; Row 2: Active Indicators)
        var row1Text = new FormattedText(
            $"{Symbol} · {Timeframe.ToUpperInvariant()}   O:{activeBar.Open:F2}  H:{activeBar.High:F2}  L:{activeBar.Low:F2}  C:{activeBar.Close:F2}   CHG:{changeSign}{change:F2} ({changeSign}{changePct:F2}%)   VOL:{activeBar.Volume:N1}",
            CultureInfo.InvariantCulture,
            FlowDirection.LeftToRight,
            new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
            9,
            change >= 0 ? bullBrush : bearBrush);
        context.DrawText(row1Text, new Point(8, 3));

        string indicatorsText = (emaStr + (string.IsNullOrWhiteSpace(stochStr) ? "" : " | " + stochStr)).TrimStart();
        if (!string.IsNullOrWhiteSpace(indicatorsText))
        {
            var row2Text = new FormattedText(
                indicatorsText,
                CultureInfo.InvariantCulture,
                FlowDirection.LeftToRight,
                new Typeface("Consolas"),
                8,
                new SolidColorBrush(Color.Parse("#C9D1D9")));
            context.DrawText(row2Text, new Point(8, 17));
        }

        // Draw Interactive Crosshair
        if (_crosshairPoint.HasValue && _crosshairPoint.Value.X < plotW && _crosshairPoint.Value.Y < (bounds.Height - bottomAxisHeight))
        {
            var pt = _crosshairPoint.Value;

            // Horizontal crosshair
            context.DrawLine(crosshairPen, new Point(0, pt.Y), new Point(plotW, pt.Y));

            // Vertical crosshair extending across both panes
            context.DrawLine(crosshairPen, new Point(pt.X, headerHeight), new Point(pt.X, bounds.Height - bottomAxisHeight));

            // Price badge on right axis (if cursor in price pane)
            if (pt.Y >= headerHeight && pt.Y <= headerHeight + pricePlotH)
            {
                double ratio = (pt.Y - headerHeight) / pricePlotH;
                decimal crosshairPrice = maxPrice - (priceRange * (decimal)ratio);
                var priceBadgeRect = new Rect(plotW + 2, pt.Y - 8, rightAxisWidth - 4, 16);
                context.DrawRectangle(new SolidColorBrush(Color.Parse("#FF9D00")), null, priceBadgeRect);
                var pBadgeText = new FormattedText(
                    crosshairPrice >= 100m ? $"{crosshairPrice:F2}" : $"{crosshairPrice:F4}",
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                    8,
                    new SolidColorBrush(Color.Parse("#000000")));
                context.DrawText(pBadgeText, new Point(plotW + 6, pt.Y - 6));
            }
            // Stochastic badge on right axis (if cursor in stochastic pane)
            else if (hasStoch && pt.Y >= stochTopY && pt.Y <= stochTopY + stochPaneH)
            {
                double ratio = (pt.Y - (stochTopY + 8.0)) / (stochPaneH - 12.0);
                decimal crosshairStoch = Math.Clamp(100m * (decimal)(1.0 - ratio), 0m, 100m);
                var stochBadgeRect = new Rect(plotW + 2, pt.Y - 7, rightAxisWidth - 4, 14);
                context.DrawRectangle(new SolidColorBrush(Color.Parse("#00E5FF")), null, stochBadgeRect);
                var sBadgeText = new FormattedText(
                    $"{crosshairStoch:F1}%",
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                    8,
                    new SolidColorBrush(Color.Parse("#000000")));
                context.DrawText(sBadgeText, new Point(plotW + 6, pt.Y - 5));
            }

            // Time badge on bottom axis
            if (!string.IsNullOrWhiteSpace(activeBar.DatetimeUtc))
            {
                string activeTime = activeBar.DatetimeUtc.Length >= 19 ? activeBar.DatetimeUtc.Substring(5, 11) : activeBar.DatetimeUtc;
                var tBadgeRect = new Rect(pt.X - 35, bounds.Height - bottomAxisHeight + 2, 70, 16);
                context.DrawRectangle(new SolidColorBrush(Color.Parse("#161B22")), borderPen, tBadgeRect);
                var tBadgeFt = new FormattedText(
                    activeTime,
                    CultureInfo.InvariantCulture,
                    FlowDirection.LeftToRight,
                    new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold),
                    8,
                    brightTextBrush);
                context.DrawText(tBadgeFt, new Point(pt.X - 30, bounds.Height - bottomAxisHeight + 4));
            }
        }
    }
}
