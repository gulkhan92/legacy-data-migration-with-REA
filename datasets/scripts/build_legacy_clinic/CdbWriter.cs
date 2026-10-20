using System.Buffers.Binary;

namespace ClinicDesk;

/// <summary>
/// Writes the clinic data file. Each record type has its own method that writes its fields in order.
/// Reference fields hold the sequence number of the record they point to.
/// </summary>
internal sealed class CdbWriter
{
    private readonly Dictionary<string, List<Dictionary<string, string>>> _csv;
    private readonly Dictionary<string, int> _patientNumbers = new();
    private readonly Dictionary<string, int> _encounterNumbers = new();

    public CdbWriter(Dictionary<string, List<Dictionary<string, string>>> csv)
    {
        _csv = csv;
        // The first row with a given identifier owns it. A duplicated row gets its own record number
        // but references keep pointing at the first one.
        var patients = csv["patients"];
        for (int i = 0; i < patients.Count; i++) _patientNumbers.TryAdd(patients[i]["Id"], i + 1);
        var encounters = csv["encounters"];
        for (int i = 0; i < encounters.Count; i++) _encounterNumbers.TryAdd(encounters[i]["Id"], i + 1);
    }

    public Dictionary<string, int> Counts => Layout.Tables.ToDictionary(t => t.Entity, t => _csv[t.Entity].Count);

    private static int Reference(Dictionary<string, int> numbers, string id)
    {
        if (string.IsNullOrWhiteSpace(id)) return 0;
        return numbers.TryGetValue(id, out var n) ? n : Legacy.Dangling;
    }

    public byte[] Write()
    {
        var sections = new List<byte[]>();
        foreach (var table in Layout.Tables)
        {
            using var stream = new MemoryStream();
            using var w = new BinaryWriter(stream);
            var rows = _csv[table.Entity];
            for (int i = 0; i < rows.Count; i++)
            {
                long start = stream.Position;
                switch (table.Entity)
                {
                    case "patients": WritePatient(w, i + 1, rows[i]); break;
                    case "encounters": WriteEncounter(w, i + 1, rows[i]); break;
                    case "conditions": WriteCondition(w, i + 1, rows[i]); break;
                    case "medications": WriteMedication(w, i + 1, rows[i]); break;
                    case "observations": WriteObservation(w, i + 1, rows[i]); break;
                }
                if (stream.Position - start != table.RecordSize)
                    throw new InvalidOperationException($"{table.Name} record is {stream.Position - start} bytes, layout says {table.RecordSize}");
            }
            w.Flush();
            sections.Add(stream.ToArray());
        }

        using var file = new MemoryStream();
        using var h = new BinaryWriter(file);
        // Header
        h.Write(System.Text.Encoding.ASCII.GetBytes(Layout.Magic));
        h.Write(Layout.Version);
        h.Write(Layout.CodePageId);
        h.Write((ushort)Layout.Tables.Length);
        h.Write((ushort)Layout.HeaderSize);
        h.Write(0);
        // Directory
        uint offset = (uint)Layout.HeaderSize;
        for (int t = 0; t < Layout.Tables.Length; t++)
        {
            var table = Layout.Tables[t];
            h.Write((ushort)table.Id);
            h.Write((ushort)table.RecordSize);
            h.Write((uint)_csv[table.Entity].Count);
            h.Write(offset);
            h.Write(0);
            offset += (uint)sections[t].Length;
        }
        foreach (var section in sections) h.Write(section);
        h.Flush();
        return file.ToArray();
    }

    private void WritePatient(BinaryWriter w, int number, Dictionary<string, string> r)
    {
        w.Write(number);
        w.Write(Legacy.Fixed(r["LAST"], 30));
        w.Write(Legacy.Fixed(r["FIRST"], 20));
        w.Write(Legacy.Days(r["BIRTHDATE"]));
        w.Write(Legacy.Days(r["DEATHDATE"]));
        w.Write(Legacy.PatientFlags(r["GENDER"], r["DEATHDATE"], r["ETHNICITY"], r["MARITAL"], r["RACE"]));
        w.Write(Legacy.Fixed(r["SSN"], 11));
        w.Write(Legacy.Fixed(r["ADDRESS"], 40));
        w.Write(Legacy.Fixed(r["CITY"], 24));
        w.Write(Legacy.Fixed(r["STATE"], 16));
        w.Write(Legacy.Fixed(r["ZIP"], 10));
        w.Write(Legacy.Cents(r["HEALTHCARE_EXPENSES"]));
        w.Write(Legacy.WholeNumber(r["INCOME"]));
    }

    private void WriteEncounter(BinaryWriter w, int number, Dictionary<string, string> r)
    {
        w.Write(number);
        w.Write(Reference(_patientNumbers, r["PATIENT"]));
        w.Write(Legacy.Minutes(r["START"]));
        w.Write(Legacy.Minutes(r["STOP"]));
        w.Write(Legacy.EncounterClass(r["ENCOUNTERCLASS"]));
        w.Write(Legacy.Fixed(r["CODE"], 16));
        w.Write(Legacy.Fixed(r["DESCRIPTION"], 80));
        var cost = new byte[4];
        BinaryPrimitives.WriteInt32BigEndian(cost, Legacy.CentsInt(r["TOTAL_CLAIM_COST"]));
        w.Write(cost);
    }

    private void WriteCondition(BinaryWriter w, int number, Dictionary<string, string> r)
    {
        w.Write(number);
        w.Write(Reference(_patientNumbers, r["PATIENT"]));
        w.Write(Reference(_encounterNumbers, r["ENCOUNTER"]));
        w.Write(Legacy.Fixed(r["CODE"], 20));
        w.Write(Legacy.Fixed(r["DESCRIPTION"], 120));
        w.Write(Legacy.Days(r["START"]));
        w.Write(Legacy.Days(r["STOP"]));
    }

    private void WriteMedication(BinaryWriter w, int number, Dictionary<string, string> r)
    {
        w.Write(number);
        w.Write(Reference(_patientNumbers, r["PATIENT"]));
        w.Write(Reference(_encounterNumbers, r["ENCOUNTER"]));
        w.Write(Legacy.Fixed(r["CODE"], 10));
        w.Write(Legacy.Fixed(r["DESCRIPTION"], 100));
        w.Write(Legacy.Days(r["START"]));
        w.Write(Legacy.Days(r["STOP"]));
        w.Write(Legacy.CentsInt(r["TOTALCOST"]));
    }

    private void WriteObservation(BinaryWriter w, int number, Dictionary<string, string> r)
    {
        w.Write(number);
        w.Write(Reference(_patientNumbers, r["PATIENT"]));
        w.Write(Reference(_encounterNumbers, r["ENCOUNTER"]));
        w.Write(Legacy.Fixed(r["CODE"], 10));
        w.Write(Legacy.Fixed(r["DESCRIPTION"], 80));
        w.Write(Legacy.Fixed(r["VALUE"], 24));
        w.Write(Legacy.Fixed(r["UNITS"], 16));
        w.Write(Legacy.Minutes(r["DATE"]));
    }
}
