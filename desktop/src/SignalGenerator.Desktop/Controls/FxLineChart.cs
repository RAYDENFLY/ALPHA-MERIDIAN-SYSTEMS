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

public class FxLineChart : Control
{
    public static readonly StyledProperty<List<FxDataPointDto>?> PointsProperty =
        AvaloniaProperty.Register<FxLineChart, List<FxDataPointDto>?>(nameof(Points));

    public static readonly StyledProperty<decimal> MinRateProperty =
        AvaloniaProperty.Register<FxLineChart, decimal>(nameof(MinRate));

    public static readonly StyledProperty<decimal> MaxRateProperty =
        AvaloniaProperty.Register<FxLineChart, decimal>(nameof(MaxRate));

    public static readonly StyledProperty<int> DecimalsProperty =
        AvaloniaProperty.Register<FxLineChart, int>(nameof(Decimals), 2);

    public static readonly StyledProperty<string> RatePrefixProperty =
        AvaloniaProperty.Register<FxLineChart, string>(nameof(RatePrefix), "Rp ");

    public static readonly StyledProperty<string> PairNameProperty =
        AvaloniaProperty.Register<FxLineChart, string>(nameof(PairName), "USD/IDR");

    public static readonly StyledProperty<string> RateTypeProperty =
        AvaloniaProperty.Register<FxLineChart, string>(nameof(RateType), "Daily Reference Rate (ECB Fix)");

    public List<FxDataPointDto>? Points
    {
        get => GetValue(PointsProperty);
        set => SetValue(PointsProperty, value);
    }

    public decimal MinRate
    {
        get => GetValue(MinRateProperty);
        set => SetValue(MinRateProperty, value);
    }

    public decimal MaxRate
    {
        get => GetValue(MaxRateProperty);
        set => SetValue(MaxRateProperty, value);
    }

    public int Decimals
    {
        get => GetValue(DecimalsProperty);
        set => SetValue(DecimalsProperty, value);
    }

    public string RatePrefix
    {
        get => GetValue(RatePrefixProperty);
        set => SetValue(RatePrefixProperty, value);
    }

    public string PairName
    {
        get => GetValue(PairNameProperty);
        set => SetValue(PairNameProperty, value);
    }

    public string RateType
    {
        get => GetValue(RateTypeProperty);
        set => SetValue(RateTypeProperty, value);
    }

    private bool _isHovering = false;
    private int _hoverIndex = -1;

    static FxLineChart()
    {
        AffectsRender<FxLineChart>(
            PointsProperty,
            MinRateProperty,
            MaxRateProperty,
            DecimalsProperty,
            RatePrefixProperty,
            PairNameProperty,
            RateTypeProperty);
    }

    public FxLineChart()
    {
        ClipToBounds = true;
    }

    protected override void OnPointerMoved(PointerEventArgs e)
    {
        base.OnPointerMoved(e);

        var pts = Points;
        if (pts == null || pts.Count == 0)
        {
            ResetHover();
            return;
        }

        var bounds = Bounds;
        double padLeft = Decimals >= 4 ? 55 : 45;
        double padRight = 15;
        double padTop = 15;
        double padBottom = 20;

        double plotW = Math.Max(10, bounds.Width - padLeft - padRight);
        double plotH = Math.Max(10, bounds.Height - padTop - padBottom);

        var pos = e.GetPosition(this);

        // Check if cursor is within plot area (with slight tolerance)
        if (pos.X >= padLeft - 2 && pos.X <= bounds.Width - padRight + 2 &&
            pos.Y >= padTop - 2 && pos.Y <= padTop + plotH + 2)
        {
            _isHovering = true;
            _hoverIndex = FindNearestObservationIndex(pos.X, padLeft, plotW, pts.Count);
            InvalidateVisual();
        }
        else
        {
            ResetHover();
        }
    }

    protected override void OnPointerExited(PointerEventArgs e)
    {
        base.OnPointerExited(e);
        ResetHover();
    }

    private void ResetHover()
    {
        if (_isHovering || _hoverIndex != -1)
        {
            _isHovering = false;
            _hoverIndex = -1;
            InvalidateVisual();
        }
    }

    /// <summary>
    /// Snaps to the nearest actual historical observation index based on X coordinate.
    /// Pure function for testability and deterministic lookup.
    /// </summary>
    public static int FindNearestObservationIndex(double mouseX, double padLeft, double plotW, int pointCount)
    {
        if (pointCount <= 0) return -1;
        if (pointCount == 1 || plotW <= 0) return 0;

        double t = (mouseX - padLeft) / plotW;
        t = Math.Clamp(t, 0.0, 1.0);
        int idx = (int)Math.Round(t * (pointCount - 1), MidpointRounding.AwayFromZero);
        return Math.Clamp(idx, 0, pointCount - 1);
    }

    /// <summary>
    /// Calculates tooltip rectangle positioned relative to anchor and clamped inside container bounds to prevent clipping.
    /// Pure function for testability.
    /// </summary>
    public static Rect CalculateTooltipBounds(Point anchor, Size tooltipSize, Size containerBounds, double margin = 6)
    {
        double w = tooltipSize.Width;
        double h = tooltipSize.Height;
        double maxW = containerBounds.Width;
        double maxH = containerBounds.Height;

        // Default: display to the right of anchor
        double x = anchor.X + 12;
        // If clipping right edge, flip to left of anchor
        if (x + w > maxW - margin)
        {
            x = anchor.X - w - 12;
        }

        // Center vertically relative to anchor
        double y = anchor.Y - h / 2;

        // Clamp safely within chart container bounds
        x = Math.Clamp(x, margin, Math.Max(margin, maxW - w - margin));
        y = Math.Clamp(y, margin, Math.Max(margin, maxH - h - margin));

        return new Rect(x, y, w, h);
    }

    public override void Render(DrawingContext context)
    {
        base.Render(context);

        var bounds = Bounds;
        if (bounds.Width <= 10 || bounds.Height <= 10) return;

        var bgBrush = new SolidColorBrush(Color.Parse("#080B0E"));
        var borderPen = new Pen(new SolidColorBrush(Color.Parse("#1C212A")), 1);
        var gridPen = new Pen(new SolidColorBrush(Color.Parse("#14181E")), 1, dashStyle: DashStyle.Dash);
        var linePen = new Pen(new SolidColorBrush(Color.Parse("#FF9D00")), 1.5);
        var areaBrush = new SolidColorBrush(Color.FromArgb(25, 255, 157, 0));
        var textBrush = new SolidColorBrush(Color.Parse("#8B949E"));
        var dotBrush = new SolidColorBrush(Color.Parse("#FF9D00"));

        // Draw background
        context.DrawRectangle(bgBrush, borderPen, new Rect(0, 0, bounds.Width, bounds.Height));

        var pts = Points;
        if (pts == null || pts.Count == 0)
        {
            var noDataText = new FormattedText("NO FX DATA AVAILABLE", CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 10, textBrush);
            context.DrawText(noDataText, new Point(bounds.Width / 2 - 60, bounds.Height / 2 - 6));
            return;
        }

        double padLeft = Decimals >= 4 ? 55 : 45;
        double padRight = 15;
        double padTop = 15;
        double padBottom = 20;

        double plotW = Math.Max(10, bounds.Width - padLeft - padRight);
        double plotH = Math.Max(10, bounds.Height - padTop - padBottom);

        decimal minVal = MinRate > 0 ? MinRate : pts.Min(p => p.Rate);
        decimal maxVal = MaxRate > 0 ? MaxRate : pts.Max(p => p.Rate);
        if (maxVal <= minVal)
        {
            maxVal = minVal + 1;
        }
        decimal range = maxVal - minVal;

        // Draw 3 horizontal grid lines (25%, 50%, 75%)
        for (int i = 1; i <= 3; i++)
        {
            double y = padTop + (plotH * (i / 4.0));
            context.DrawLine(gridPen, new Point(padLeft, y), new Point(bounds.Width - padRight, y));

            decimal gridRate = maxVal - (range * (decimal)(i / 4.0));
            var gridText = new FormattedText(FormatAxisPrice(gridRate), CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
            context.DrawText(gridText, new Point(4, y - 5));
        }

        // Draw top and bottom labels
        var topText = new FormattedText(FormatAxisPrice(maxVal), CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
        context.DrawText(topText, new Point(4, padTop - 5));

        var botText = new FormattedText(FormatAxisPrice(minVal), CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
        context.DrawText(botText, new Point(4, padTop + plotH - 5));

        // Calculate plot coordinates
        int count = pts.Count;
        var screenPoints = new List<Point>(count);

        for (int i = 0; i < count; i++)
        {
            double x = count > 1 ? padLeft + ((double)i / (count - 1)) * plotW : padLeft + plotW / 2;
            decimal r = pts[i].Rate;
            double norm = (double)((r - minVal) / range);
            norm = Math.Clamp(norm, 0.0, 1.0);
            double y = padTop + (1.0 - norm) * plotH;
            screenPoints.Add(new Point(x, y));
        }

        // Draw filled area under curve
        var streamGeo = new StreamGeometry();
        using (var sgc = streamGeo.Open())
        {
            sgc.BeginFigure(new Point(screenPoints[0].X, padTop + plotH), isFilled: true);
            foreach (var sp in screenPoints)
            {
                sgc.LineTo(sp);
            }
            sgc.LineTo(new Point(screenPoints[^1].X, padTop + plotH));
            sgc.EndFigure(isClosed: true);
        }
        context.DrawGeometry(areaBrush, null, streamGeo);

        // Draw line segments
        for (int i = 0; i < screenPoints.Count - 1; i++)
        {
            context.DrawLine(linePen, screenPoints[i], screenPoints[i + 1]);
        }

        // Draw active dot at the last point
        var lastPt = screenPoints[^1];
        context.DrawEllipse(dotBrush, null, lastPt, 3, 3);

        // Draw dates (start and end)
        if (pts.Count > 0)
        {
            string startLabel = pts[0].Date.ToString("yyyy-MM-dd");
            string endLabel = pts[^1].Date.ToString("yyyy-MM-dd");

            var startText = new FormattedText(startLabel, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
            context.DrawText(startText, new Point(padLeft, bounds.Height - padBottom + 4));

            var endText = new FormattedText(endLabel, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
            context.DrawText(endText, new Point(bounds.Width - padRight - 55, bounds.Height - padBottom + 4));
        }

        // -------------------------------------------------------------
        // INTERACTIVE CROSSHAIR & TOOLTIP OVERLAY
        // -------------------------------------------------------------
        if (_isHovering && _hoverIndex >= 0 && _hoverIndex < count)
        {
            var snappedPt = pts[_hoverIndex];
            var snappedCoord = screenPoints[_hoverIndex];
            double sx = snappedCoord.X;
            double sy = snappedCoord.Y;

            // Crosshair pens
            var crosshairPen = new Pen(new SolidColorBrush(Color.Parse("#384252")), 1, dashStyle: DashStyle.Dash);
            var activePen = new Pen(new SolidColorBrush(Color.Parse("#FF9D00")), 1);
            var glowBrush = new SolidColorBrush(Color.FromArgb(80, 255, 157, 0));
            var whiteBrush = new SolidColorBrush(Color.Parse("#FFFFFF"));
            var badgeBg = new SolidColorBrush(Color.Parse("#161B22"));
            var badgeBorder = new Pen(new SolidColorBrush(Color.Parse("#30363D")), 1);

            // 1. Draw Vertical Crosshair (from padTop to padTop + plotH)
            context.DrawLine(crosshairPen, new Point(sx, padTop), new Point(sx, padTop + plotH));

            // 2. Draw Horizontal Crosshair (from padLeft to bounds.Width - padRight)
            context.DrawLine(crosshairPen, new Point(padLeft, sy), new Point(bounds.Width - padRight, sy));

            // 3. Draw Snapped Observation Indicator Dot
            context.DrawEllipse(glowBrush, null, new Point(sx, sy), 6, 6);
            context.DrawEllipse(dotBrush, activePen, new Point(sx, sy), 3, 3);

            // 4. Draw Date Badge on Bottom Axis
            string dateStr = snappedPt.Date.ToString("yyyy-MM-dd");
            var dateBadgeText = new FormattedText(dateStr, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, whiteBrush);
            double dateBadgeW = dateBadgeText.Width + 8;
            double dateBadgeH = dateBadgeText.Height + 4;
            double dateBadgeX = Math.Clamp(sx - dateBadgeW / 2, padLeft, bounds.Width - padRight - dateBadgeW);
            double dateBadgeY = bounds.Height - padBottom + 2;
            context.DrawRectangle(badgeBg, badgeBorder, new Rect(dateBadgeX, dateBadgeY, dateBadgeW, dateBadgeH), 2, 2);
            context.DrawText(dateBadgeText, new Point(dateBadgeX + 4, dateBadgeY + 2));

            // 5. Draw Price Badge on Right Axis
            string priceStr = FormatAxisPrice(snappedPt.Rate);
            var priceBadgeText = new FormattedText(priceStr, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, dotBrush);
            double priceBadgeW = priceBadgeText.Width + 8;
            double priceBadgeH = priceBadgeText.Height + 4;
            double priceBadgeX = bounds.Width - padRight;
            double priceBadgeY = Math.Clamp(sy - priceBadgeH / 2, padTop, padTop + plotH - priceBadgeH);
            context.DrawRectangle(badgeBg, badgeBorder, new Rect(priceBadgeX, priceBadgeY, priceBadgeW, priceBadgeH), 2, 2);
            context.DrawText(priceBadgeText, new Point(priceBadgeX + 4, priceBadgeY + 2));

            // 6. Floating Tooltip Card (Zero-clipping, bounds-aware)
            RenderFloatingTooltip(context, bounds, snappedPt, new Point(sx, sy));
        }
    }

    private void RenderFloatingTooltip(DrawingContext context, Rect bounds, FxDataPointDto pt, Point anchor)
    {
        var titleBrush = new SolidColorBrush(Color.Parse("#FF9D00"));
        var rateBrush = new SolidColorBrush(Color.Parse("#00E676"));
        var labelBrush = new SolidColorBrush(Color.Parse("#8B949E"));
        var subBrush = new SolidColorBrush(Color.Parse("#545D68"));
        var cardBg = new SolidColorBrush(Color.FromArgb(240, 15, 19, 24));
        var cardBorder = new Pen(new SolidColorBrush(Color.Parse("#2D3748")), 1);

        string format = Decimals switch
        {
            2 => "N2",
            3 => "N3",
            4 => "N4",
            5 => "N5",
            6 => "N6",
            _ => "N4"
        };

        string formattedRate = $"{RatePrefix}{pt.Rate.ToString(format, CultureInfo.InvariantCulture)}";
        string header = $"■ {PairName} // {pt.Date:yyyy-MM-dd}";

        var headerText = new FormattedText(header, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold), 9, titleBrush);
        var rateText = new FormattedText(formattedRate, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas", FontStyle.Normal, FontWeight.Bold), 11, rateBrush);

        FormattedText detailText;
        if (pt.IsOhlc && pt.Open is decimal o && pt.High is decimal h && pt.Low is decimal l && pt.Close is decimal c)
        {
            string ohlcStr = $"O:{o.ToString(format, CultureInfo.InvariantCulture)} H:{h.ToString(format, CultureInfo.InvariantCulture)} L:{l.ToString(format, CultureInfo.InvariantCulture)} C:{c.ToString(format, CultureInfo.InvariantCulture)}";
            detailText = new FormattedText(ohlcStr, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, labelBrush);
        }
        else
        {
            string typeStr = $"RATE: {RateType}";
            detailText = new FormattedText(typeStr, CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, subBrush);
        }

        double cardW = Math.Max(headerText.Width, Math.Max(rateText.Width, detailText.Width)) + 16;
        double cardH = headerText.Height + rateText.Height + detailText.Height + 14;

        var cardRect = CalculateTooltipBounds(anchor, new Size(cardW, cardH), new Size(bounds.Width, bounds.Height));

        context.DrawRectangle(cardBg, cardBorder, cardRect, 3, 3);
        context.DrawText(headerText, new Point(cardRect.X + 8, cardRect.Y + 4));
        context.DrawText(rateText, new Point(cardRect.X + 8, cardRect.Y + 4 + headerText.Height + 2));
        context.DrawText(detailText, new Point(cardRect.X + 8, cardRect.Y + 4 + headerText.Height + rateText.Height + 4));
    }

    private string FormatAxisPrice(decimal value)
    {
        if (Decimals >= 5) return value.ToString("F6", CultureInfo.InvariantCulture);
        if (Decimals >= 3) return value.ToString("F4", CultureInfo.InvariantCulture);
        if (value >= 1000) return value.ToString("F0", CultureInfo.InvariantCulture);
        if (value >= 10) return value.ToString("F2", CultureInfo.InvariantCulture);
        return value.ToString("F4", CultureInfo.InvariantCulture);
    }
}
