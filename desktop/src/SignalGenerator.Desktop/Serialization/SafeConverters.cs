using System;
using System.Globalization;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace SignalGenerator.Desktop.Serialization;

/// <summary>
/// Bulletproof converter for decimal that safely parses null, numbers, strings (with currency symbols or commas),
/// scientific notation, or floats without throwing JsonException.
/// </summary>
public class SafeDecimalConverter : JsonConverter<decimal>
{
    public override decimal Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return 0m;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetDecimal(out var dec))
            {
                return dec;
            }
            if (reader.TryGetDouble(out var dbl))
            {
                return Convert.ToDecimal(dbl);
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return 0m;
            }

            str = str.Replace("$", string.Empty).Replace(",", string.Empty).Trim();
            if (decimal.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dec))
            {
                return dec;
            }
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return Convert.ToDecimal(dbl);
            }
        }

        return 0m;
    }

    public override void Write(Utf8JsonWriter writer, decimal value, JsonSerializerOptions options)
    {
        writer.WriteNumberValue(value);
    }
}

/// <summary>
/// Bulletproof converter for nullable decimal that safely returns null on JsonTokenType.Null or empty string.
/// </summary>
public class SafeNullableDecimalConverter : JsonConverter<decimal?>
{
    public override decimal? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return null;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetDecimal(out var dec))
            {
                return dec;
            }
            if (reader.TryGetDouble(out var dbl))
            {
                return Convert.ToDecimal(dbl);
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return null;
            }

            str = str.Replace("$", string.Empty).Replace(",", string.Empty).Trim();
            if (decimal.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dec))
            {
                return dec;
            }
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return Convert.ToDecimal(dbl);
            }
        }

        return null;
    }

    public override void Write(Utf8JsonWriter writer, decimal? value, JsonSerializerOptions options)
    {
        if (value.HasValue)
        {
            writer.WriteNumberValue(value.Value);
        }
        else
        {
            writer.WriteNullValue();
        }
    }
}

/// <summary>
/// Bulletproof converter for double that safely parses numbers, strings, or null.
/// </summary>
public class SafeDoubleConverter : JsonConverter<double>
{
    public override double Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return 0.0;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetDouble(out var dbl))
            {
                return dbl;
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return 0.0;
            }

            str = str.Replace("$", string.Empty).Replace(",", string.Empty).Trim();
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return dbl;
            }
        }

        return 0.0;
    }

    public override void Write(Utf8JsonWriter writer, double value, JsonSerializerOptions options)
    {
        writer.WriteNumberValue(value);
    }
}

/// <summary>
/// Bulletproof converter for nullable double.
/// </summary>
public class SafeNullableDoubleConverter : JsonConverter<double?>
{
    public override double? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return null;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetDouble(out var dbl))
            {
                return dbl;
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return null;
            }

            str = str.Replace("$", string.Empty).Replace(",", string.Empty).Trim();
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return dbl;
            }
        }

        return null;
    }

    public override void Write(Utf8JsonWriter writer, double? value, JsonSerializerOptions options)
    {
        if (value.HasValue)
        {
            writer.WriteNumberValue(value.Value);
        }
        else
        {
            writer.WriteNullValue();
        }
    }
}

/// <summary>
/// Bulletproof converter for long that safely parses numbers, strings, or null.
/// </summary>
public class SafeLongConverter : JsonConverter<long>
{
    public override long Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return 0;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetInt64(out var val))
            {
                return val;
            }
            if (reader.TryGetDouble(out var dbl))
            {
                return (long)dbl;
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return 0;
            }

            if (long.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var val))
            {
                return val;
            }
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return (long)dbl;
            }
        }

        return 0;
    }

    public override void Write(Utf8JsonWriter writer, long value, JsonSerializerOptions options)
    {
        writer.WriteNumberValue(value);
    }
}

/// <summary>
/// Bulletproof converter for nullable long.
/// </summary>
public class SafeNullableLongConverter : JsonConverter<long?>
{
    public override long? Read(ref Utf8JsonReader reader, Type typeToConvert, JsonSerializerOptions options)
    {
        if (reader.TokenType == JsonTokenType.Null)
        {
            return null;
        }

        if (reader.TokenType == JsonTokenType.Number)
        {
            if (reader.TryGetInt64(out var val))
            {
                return val;
            }
            if (reader.TryGetDouble(out var dbl))
            {
                return (long)dbl;
            }
        }

        if (reader.TokenType == JsonTokenType.String)
        {
            var str = reader.GetString();
            if (string.IsNullOrWhiteSpace(str))
            {
                return null;
            }

            if (long.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var val))
            {
                return val;
            }
            if (double.TryParse(str, NumberStyles.Any, CultureInfo.InvariantCulture, out var dbl))
            {
                return (long)dbl;
            }
        }

        return null;
    }

    public override void Write(Utf8JsonWriter writer, long? value, JsonSerializerOptions options)
    {
        if (value.HasValue)
        {
            writer.WriteNumberValue(value.Value);
        }
        else
        {
            writer.WriteNullValue();
        }
    }
}
