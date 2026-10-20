using ClinicDesk;

// ClinicDesk: imports exported clinic CSV files into the proprietary clinic data file (.cdb).
//
//   ClinicDesk import <csv-folder> <output.cdb> [layout.json]
//
// The CSV folder holds patients.csv, encounters.csv, conditions.csv, medications.csv and
// observations.csv. The optional layout file receives the true record layout (used only by the
// evaluation harness, never by the migration platform).

if (args.Length < 3 || args[0] != "import")
{
    Console.Error.WriteLine("usage: ClinicDesk import <csv-folder> <output.cdb> [layout.json]");
    return 2;
}

var folder = args[1];
var csv = new Dictionary<string, List<Dictionary<string, string>>>();
foreach (var table in Layout.Tables)
{
    var path = Path.Combine(folder, table.Entity + ".csv");
    if (!File.Exists(path))
    {
        Console.Error.WriteLine($"missing {path}");
        return 1;
    }
    csv[table.Entity] = Csv.Read(path);
}

var writer = new CdbWriter(csv);
var bytes = writer.Write();
File.WriteAllBytes(args[2], bytes);
if (args.Length > 3) File.WriteAllText(args[3], Layout.ToJson(writer.Counts));

Console.WriteLine($"wrote {args[2]}: {bytes.Length} bytes, " +
    string.Join(", ", writer.Counts.Select(kv => $"{kv.Key}={kv.Value}")));
return 0;
