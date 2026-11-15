namespace PartsUnlimited.Models;

/// <summary>Товар каталога автозапчастей.</summary>
public sealed record Product(
    int Id,
    string Sku,
    string Title,
    string Category,
    string CarMake,
    decimal Price,
    int Stock)
{
    public bool InStock => Stock > 0;
}
