using System.Text.Json;
using SignalGenerator.Desktop.Serialization;
using SignalGenerator.Desktop.Services;
using Xunit;

namespace SignalGenerator.Desktop.Tests;

public class SafeConvertersTests
{
    private class TestContainer
    {
        public decimal Amount { get; set; }
        public decimal? NullableAmount { get; set; }
        public double Rate { get; set; }
        public double? NullableRate { get; set; }
        public long Timestamp { get; set; }
        public long? NullableTimestamp { get; set; }
    }

    [Fact]
    public void SafeConverters_SafelyParseAllEdgeCases()
    {
        const string json = """
        {
          "Amount": "$12,345.67",
          "NullableAmount": null,
          "Rate": "1.2345e2",
          "NullableRate": null,
          "Timestamp": "1700000000",
          "NullableTimestamp": null
        }
        """;

        var result = JsonSerializer.Deserialize<TestContainer>(json, ApiClient.JsonOptions);

        Assert.NotNull(result);
        Assert.Equal(12345.67m, result.Amount);
        Assert.Null(result.NullableAmount);
        Assert.Equal(123.45, result.Rate, 2);
        Assert.Null(result.NullableRate);
        Assert.Equal(1700000000L, result.Timestamp);
        Assert.Null(result.NullableTimestamp);
    }

    [Fact]
    public void SafeConverters_HandleEmptyAndMalformedGracefully()
    {
        const string json = """
        {
          "Amount": "",
          "NullableAmount": "",
          "Rate": "",
          "NullableRate": "",
          "Timestamp": "",
          "NullableTimestamp": ""
        }
        """;

        var result = JsonSerializer.Deserialize<TestContainer>(json, ApiClient.JsonOptions);

        Assert.NotNull(result);
        Assert.Equal(0m, result.Amount);
        Assert.Null(result.NullableAmount);
        Assert.Equal(0.0, result.Rate);
        Assert.Null(result.NullableRate);
        Assert.Equal(0L, result.Timestamp);
        Assert.Null(result.NullableTimestamp);
    }
}
