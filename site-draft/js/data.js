// Каталог автомобилей. Пока хранится прямо в коде; позже переедет на сервер/в базу.
// body: sedan | crossover | suv | hatch
// condition: new | used
const CARS = [
  { id: 1,  brand: "Haval",   model: "Jolion",        year: 2025, price: 2199000, mileage: 0,      body: "crossover", fuel: "Бензин",  transmission: "Робот",   drive: "Передний", engine: 1.5, power: 150, color: "#C8CDD3", colorName: "Серебристый", condition: "new" },
  { id: 2,  brand: "Chery",   model: "Tiggo 7 Pro Max", year: 2025, price: 2789000, mileage: 0,    body: "crossover", fuel: "Бензин",  transmission: "Вариатор", drive: "Полный",  engine: 1.6, power: 186, color: "#1F3B5C", colorName: "Синий",       condition: "new" },
  { id: 3,  brand: "Geely",   model: "Monjaro",       year: 2025, price: 4299000, mileage: 0,      body: "suv",       fuel: "Бензин",  transmission: "Автомат", drive: "Полный",   engine: 2.0, power: 238, color: "#2A2D33", colorName: "Чёрный",      condition: "new" },
  { id: 4,  brand: "Lada",    model: "Vesta NG",      year: 2025, price: 1599000, mileage: 0,      body: "sedan",     fuel: "Бензин",  transmission: "Механика", drive: "Передний", engine: 1.6, power: 106, color: "#9E2A2B", colorName: "Красный",     condition: "new" },
  { id: 5,  brand: "Toyota",  model: "Camry",         year: 2021, price: 3150000, mileage: 68000,  body: "sedan",     fuel: "Бензин",  transmission: "Автомат", drive: "Передний", engine: 2.5, power: 200, color: "#EDEDEA", colorName: "Белый",       condition: "used" },
  { id: 6,  brand: "Kia",     model: "K5",            year: 2022, price: 2690000, mileage: 41000,  body: "sedan",     fuel: "Бензин",  transmission: "Автомат", drive: "Передний", engine: 2.5, power: 194, color: "#4A5560", colorName: "Графит",      condition: "used" },
  { id: 7,  brand: "Hyundai", model: "Creta",         year: 2020, price: 1890000, mileage: 87000,  body: "crossover", fuel: "Бензин",  transmission: "Автомат", drive: "Полный",   engine: 2.0, power: 149, color: "#B5652B", colorName: "Оранжевый",   condition: "used" },
  { id: 8,  brand: "BMW",     model: "X5 xDrive30d",  year: 2019, price: 5490000, mileage: 112000, body: "suv",       fuel: "Дизель",  transmission: "Автомат", drive: "Полный",   engine: 3.0, power: 249, color: "#1B1E24", colorName: "Чёрный",      condition: "used" },
  { id: 9,  brand: "Tank",    model: "300",           year: 2025, price: 4799000, mileage: 0,      body: "suv",       fuel: "Бензин",  transmission: "Автомат", drive: "Полный",   engine: 2.0, power: 220, color: "#5B6348", colorName: "Хаки",        condition: "new" },
  { id: 10, brand: "Changan", model: "UNI-K",         year: 2024, price: 3599000, mileage: 9000,   body: "crossover", fuel: "Бензин",  transmission: "Автомат", drive: "Полный",   engine: 2.0, power: 226, color: "#7A1F2B", colorName: "Бордовый",    condition: "used" },
  { id: 11, brand: "Omoda",   model: "C5",            year: 2025, price: 2349000, mileage: 0,      body: "crossover", fuel: "Бензин",  transmission: "Вариатор", drive: "Передний", engine: 1.5, power: 147, color: "#3C7A89", colorName: "Бирюзовый",  condition: "new" },
  { id: 12, brand: "Volkswagen", model: "Polo",       year: 2019, price: 1290000, mileage: 96000,  body: "hatch",     fuel: "Бензин",  transmission: "Автомат", drive: "Передний", engine: 1.6, power: 110, color: "#D9D4C7", colorName: "Бежевый",     condition: "used" },
  { id: 13, brand: "Li Auto", model: "L7",            year: 2024, price: 5890000, mileage: 15000,  body: "suv",       fuel: "Гибрид",  transmission: "Редуктор", drive: "Полный",  engine: 1.5, power: 449, color: "#6E7479", colorName: "Серый",       condition: "used" },
  { id: 14, brand: "Kia",     model: "Rio X",         year: 2021, price: 1450000, mileage: 54000,  body: "hatch",     fuel: "Бензин",  transmission: "Автомат", drive: "Передний", engine: 1.6, power: 123, color: "#2F5D8A", colorName: "Синий",       condition: "used" },
];

const BODY_LABELS = { sedan: "Седан", crossover: "Кроссовер", suv: "Внедорожник", hatch: "Хэтчбек" };

const DEALER = {
  name: "Тачки",
  address: "Москва, Варшавское шоссе, 170",
  phone: "+7 (495) 000-00-00",
  hours: "Ежедневно 9:00–21:00",
  creditRate: 19.9, // % годовых по умолчанию
};
