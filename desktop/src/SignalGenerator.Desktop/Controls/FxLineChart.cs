using System;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;
using Avalonia;
using Avalonia.Controls;
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

    static FxLineChart()
    {
        AffectsRender<FxLineChart>(PointsProperty, MinRateProperty, MaxRateProperty);
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

        double padLeft = 45;
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
            var gridText = new FormattedText($"{gridRate:F0}", CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
            context.DrawText(gridText, new Point(4, y - 5));
        }

        // Draw top and bottom labels
        var topText = new FormattedText($"{maxVal:F0}", CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
        context.DrawText(topText, new Point(4, padTop - 5));

        var botText = new FormattedText($"{minVal:F0}", CultureInfo.InvariantCulture, FlowDirection.LeftToRight, new Typeface("Consolas"), 8, textBrush);
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
    }
}
