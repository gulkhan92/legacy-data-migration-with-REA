using System.Text.Json;
using System.Text.Json.Serialization;

namespace ClinicDesk;

/// <summary>One stored field. Offsets are computed from the order and sizes below.</summary>
internal sealed record Field(
    string Name, string Type, int Size, string Semantic,
    string? Encoding = null, string? Endianness = null, string? Note = null);

internal sealed record TableSpec(int Id, string Name, string Entity, Field[] Fields)
{
    public int RecordSize => Fields.Sum(f => f.Size);
}

/// <summary>The file layout. The writer in CdbWriter.cs must produce exactly these records.</summary>
internal static class Layout
{
    public const string Magic = "CDB1";
    public const ushort Version = 1;
    public const ushort CodePageId = 1252;
    public const int DirectoryOffset = 16;
    public const int DirectoryEntrySize = 16;

    private const string Cp = "windows-1252";
    private const string Le = "little";
    private const string Be = "big";

    public static readonly TableSpec Patients = new(1, "PATIENT", "patients", new[]
    {
        new Field("PatNo", "int32", 4, "key", Endianness: Le),
        new Field("LastNm", "char", 30, "text", Cp),
        new Field("FirstNm", "char", 20, "text", Cp),
        new Field("BirthDt", "int32", 4, "epoch_days", Endianness: Le),
        new Field("DeathDt", "int32", 4, "epoch_days", Endianness: Le),
        new Field("Flags", "uint16", 2, "bit_flags", Endianness: Le, Note: "bit 0 male, bit 1 deceased, bit 2 hispanic, bit 3 married, bits 4-5 race (0 white, 1 black, 2 asian, 3 other)"),
        new Field("Ssn", "char", 11, "text", Cp),
        new Field("Addr1", "char", 40, "text", Cp),
        new Field("CityNm", "char", 24, "text", Cp),
        new Field("StateNm", "char", 16, "text", Cp),
        new Field("ZipCd", "char", 10, "text", Cp),
        new Field("ExpCents", "int64", 8, "cents", Endianness: Le, Note: "lifetime healthcare expenses, no target field"),
        new Field("IncomeAmt", "int32", 4, "whole_number", Endianness: Le, Note: "annual income in whole dollars, no target field"),
    });

    public static readonly TableSpec Encounters = new(2, "ENCNTR", "encounters", new[]
    {
        new Field("EncNo", "int32", 4, "key", Endianness: Le),
        new Field("PatNo", "int32", 4, "ref", Endianness: Le),
        new Field("StartMin", "int32", 4, "epoch_minutes", Endianness: Le),
        new Field("EndMin", "int32", 4, "epoch_minutes", Endianness: Le),
        new Field("ClassCd", "uint8", 1, "code"),
        new Field("TypeCd", "char", 16, "text", Cp, Note: "procedure code of the visit type, no target field"),
        new Field("DescTxt", "char", 80, "text", Cp),
        new Field("CostCents", "int32", 4, "cents", Endianness: Be, Note: "the only big endian field"),
    });

    public static readonly TableSpec Conditions = new(3, "COND", "conditions", new[]
    {
        new Field("CondNo", "int32", 4, "key", Endianness: Le),
        new Field("PatNo", "int32", 4, "ref", Endianness: Le),
        new Field("EncNo", "int32", 4, "ref", Endianness: Le),
        new Field("DxCd", "char", 20, "text", Cp),
        new Field("DxDesc", "char", 120, "text", Cp),
        new Field("OnsetDt", "int32", 4, "epoch_days", Endianness: Le),
        new Field("ResolvDt", "int32", 4, "epoch_days", Endianness: Le),
    });

    public static readonly TableSpec Medications = new(4, "MEDS", "medications", new[]
    {
        new Field("MedNo", "int32", 4, "key", Endianness: Le),
        new Field("PatNo", "int32", 4, "ref", Endianness: Le),
        new Field("EncNo", "int32", 4, "ref", Endianness: Le),
        new Field("RxCd", "char", 10, "text", Cp),
        new Field("RxDesc", "char", 100, "text", Cp),
        new Field("StartDt", "int32", 4, "epoch_days", Endianness: Le),
        new Field("StopDt", "int32", 4, "epoch_days", Endianness: Le),
        new Field("CostCents", "int32", 4, "cents", Endianness: Le),
    });

    public static readonly TableSpec Observations = new(5, "OBS", "observations", new[]
    {
        new Field("ObsNo", "int32", 4, "key", Endianness: Le),
        new Field("PatNo", "int32", 4, "ref", Endianness: Le),
        new Field("EncNo", "int32", 4, "ref", Endianness: Le),
        new Field("ObsCd", "char", 10, "text", Cp),
        new Field("ObsDesc", "char", 80, "text", Cp),
        new Field("ObsVal", "char", 24, "text", Cp),
        new Field("ObsUnit", "char", 16, "text", Cp),
        new Field("ObsMin", "int32", 4, "epoch_minutes", Endianness: Le),
    });

    public static readonly TableSpec[] Tables = { Patients, Encounters, Conditions, Medications, Observations };

    public static int HeaderSize => DirectoryOffset + Tables.Length * DirectoryEntrySize;

    private static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull,
    };

    /// <summary>The true layout, written next to the data file for the evaluation harness.</summary>
    public static string ToJson(IReadOnlyDictionary<string, int> counts)
    {
        long offset = HeaderSize;
        var tables = new List<object>();
        foreach (var t in Tables)
        {
            int fieldOffset = 0;
            var fields = new List<object>();
            foreach (var f in t.Fields)
            {
                fields.Add(new
                {
                    name = f.Name, offset = fieldOffset, length = f.Size, type = f.Type, semantic = f.Semantic,
                    encoding = f.Encoding, endianness = f.Endianness, note = f.Note,
                });
                fieldOffset += f.Size;
            }
            int count = counts[t.Entity];
            tables.Add(new
            {
                id = t.Id, name = t.Name, entity = t.Entity, record_size = t.RecordSize,
                record_count = count, data_offset = offset, fields,
            });
            offset += (long)count * t.RecordSize;
        }
        var doc = new
        {
            format = Magic, version = (int)Version, code_page = (int)CodePageId,
            header = new
            {
                size = HeaderSize,
                fields = new object[]
                {
                    new { name = "magic", offset = 0, length = 4, type = "char", encoding = "ascii" },
                    new { name = "version", offset = 4, length = 2, type = "uint16", endianness = Le },
                    new { name = "code_page", offset = 6, length = 2, type = "uint16", endianness = Le },
                    new { name = "table_count", offset = 8, length = 2, type = "uint16", endianness = Le },
                    new { name = "header_size", offset = 10, length = 2, type = "uint16", endianness = Le },
                    new { name = "reserved", offset = 12, length = 4, type = "bytes" },
                },
                directory = new
                {
                    offset = DirectoryOffset, entry_size = DirectoryEntrySize,
                    entry_fields = new object[]
                    {
                        new { name = "table_id", offset = 0, length = 2, type = "uint16", endianness = Le },
                        new { name = "record_size", offset = 2, length = 2, type = "uint16", endianness = Le },
                        new { name = "record_count", offset = 4, length = 4, type = "uint32", endianness = Le },
                        new { name = "data_offset", offset = 8, length = 4, type = "uint32", endianness = Le },
                        new { name = "reserved", offset = 12, length = 4, type = "bytes" },
                    },
                },
            },
            file_size = offset,
            conventions = new
            {
                epoch = "1900-01-01",
                null_date = 0,
                invalid_date = Legacy.InvalidDate,
                dangling_reference = Legacy.Dangling,
                no_reference = 0,
                null_text = "all spaces",
                text_padding = "space (0x20), longer values are truncated",
                money = "integer cents",
                timestamps = "whole minutes since the epoch, seconds are dropped",
            },
            tables,
        };
        return JsonSerializer.Serialize(doc, Json) + "\n";
    }
}
