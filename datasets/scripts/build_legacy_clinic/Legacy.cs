using System.Globalization;
using System.Text;

namespace ClinicDesk;

/// <summary>Storage conventions of the legacy clinic system.</summary>
internal static class Legacy
{
    /// <summary>Dates are whole days since this day, timestamps are whole minutes since midnight of it.</summary>
    public static readonly DateTime Epoch = new(1900, 1, 1, 0, 0, 0, DateTimeKind.Utc);

    /// <summary>Stored in a date field when the text cannot be read as a date.</summary>
    public const int InvalidDate = -1;

    /// <summary>Stored in a reference field when the referenced record does not exist.</summary>
    public const int Dangling = 9_999_999;

    /// <summary>The legacy Windows code page. Characters it cannot represent become a question mark.</summary>
    public static readonly Encoding CodePage = CreateCodePage();

    private static Encoding CreateCodePage()
    {
        Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);
        return Encoding.GetEncoding(1252, new EncoderReplacementFallback("?"), DecoderFallback.ReplacementFallback);
    }

    /// <summary>Whole days since 1900-01-01. Empty text is 0 (no date), unreadable text is -1.</summary>
    public static int Days(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return 0;
        if (value.Length >= 10 &&
            DateTime.TryParseExact(value[..10], "yyyy-MM-dd", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var date))
        {
            return (int)(date.Date - Epoch).TotalDays;
        }
        return InvalidDate;
    }

    /// <summary>Whole minutes since 1900-01-01 00:00, seconds are dropped. Empty is 0, unreadable is -1.</summary>
    public static int Minutes(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return 0;
        if (DateTime.TryParseExact(value, "yyyy-MM-dd'T'HH:mm:ss'Z'", CultureInfo.InvariantCulture,
                DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out var stamp))
        {
            return (int)Math.Floor((stamp - Epoch).TotalMinutes);
        }
        return InvalidDate;
    }

    /// <summary>Money as whole cents. Empty is 0, unreadable is the minimum value of the type.</summary>
    public static long Cents(string value)
    {
        if (string.IsNullOrWhiteSpace(value)) return 0;
        if (decimal.TryParse(value, NumberStyles.Float, CultureInfo.InvariantCulture, out var amount))
        {
            return (long)Math.Round(amount * 100m, MidpointRounding.AwayFromZero);
        }
        return long.MinValue;
    }

    public static int CentsInt(string value)
    {
        var cents = Cents(value);
        if (cents == long.MinValue || cents > int.MaxValue || cents < int.MinValue) return int.MinValue;
        return (int)cents;
    }

    public static int WholeNumber(string value) =>
        int.TryParse(value, NumberStyles.Integer, CultureInfo.InvariantCulture, out var n) ? n : 0;

    /// <summary>Fixed width text in the legacy code page, truncated or padded with spaces.</summary>
    public static byte[] Fixed(string? value, int width)
    {
        var bytes = CodePage.GetBytes(value ?? string.Empty);
        var result = new byte[width];
        Array.Fill(result, (byte)' ');
        Array.Copy(bytes, result, Math.Min(bytes.Length, width));
        return result;
    }

    public static byte EncounterClass(string value) => value.ToLowerInvariant() switch
    {
        "ambulatory" => 1,
        "emergency" => 2,
        "inpatient" => 3,
        "wellness" => 4,
        "urgentcare" => 5,
        "outpatient" => 6,
        "virtual" => 7,
        "hospice" => 8,
        "home" => 9,
        "snf" => 10,
        _ => 0,
    };

    /// <summary>Packed patient flags: bit 0 male, bit 1 deceased, bit 2 hispanic, bit 3 married, bits 4-5 race.</summary>
    public static ushort PatientFlags(string gender, string deathDate, string ethnicity, string marital, string race)
    {
        int flags = 0;
        if (gender.Equals("M", StringComparison.OrdinalIgnoreCase)) flags |= 1 << 0;
        if (!string.IsNullOrWhiteSpace(deathDate)) flags |= 1 << 1;
        if (ethnicity.Equals("hispanic", StringComparison.OrdinalIgnoreCase)) flags |= 1 << 2;
        if (marital.Equals("M", StringComparison.OrdinalIgnoreCase)) flags |= 1 << 3;
        int raceCode = race.ToLowerInvariant() switch { "white" => 0, "black" => 1, "asian" => 2, _ => 3 };
        flags |= raceCode << 4;
        return (ushort)flags;
    }
}
