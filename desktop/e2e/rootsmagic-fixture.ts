/** Builds a fictional SQLite source exclusively for packaged RootsMagic verification. */
import { DatabaseSync } from 'node:sqlite'
import { existsSync } from 'node:fs'

/** Creates a new bounded fixture; never opens or replaces an existing database. */
export function createRootsMagicFixture(path: string): void {
  if (existsSync(path)) throw new Error('Fictional RootsMagic fixture must use a new path')
  const database = new DatabaseSync(path)
  try {
    database.exec(`
      CREATE TABLE PersonTable(PersonID INTEGER PRIMARY KEY, Sex INTEGER, Living INTEGER);
      CREATE TABLE NameTable(NameID INTEGER PRIMARY KEY, OwnerID INTEGER, Given TEXT, Surname TEXT, IsPrimary INTEGER);
      CREATE TABLE FamilyTable(FamilyID INTEGER PRIMARY KEY, FatherID INTEGER, MotherID INTEGER);
      CREATE TABLE ChildTable(FamilyID INTEGER, ChildID INTEGER);
      CREATE TABLE EventTable(EventID INTEGER PRIMARY KEY, OwnerID INTEGER, OwnerType INTEGER, EventType INTEGER, Date TEXT, PlaceID INTEGER);
      CREATE TABLE FactTypeTable(FactTypeID INTEGER PRIMARY KEY, Name TEXT, GedcomTag TEXT);
      CREATE TABLE PlaceTable(PlaceID INTEGER PRIMARY KEY, Name TEXT);
      INSERT INTO FamilyTable VALUES (1,1,2);
      INSERT INTO ChildTable VALUES (1,3);
      INSERT INTO FactTypeTable VALUES (1,'Birth','BIRT');
      INSERT INTO PlaceTable VALUES (1,'Fictional County');
      INSERT INTO EventTable VALUES (1,1,0,1,'D.+19000101..+00000000..',1);
    `)
    const person = database.prepare('INSERT INTO PersonTable VALUES (?,?,?)')
    const name = database.prepare('INSERT INTO NameTable VALUES (?,?,?,?,1)')
    for (let id = 1; id <= 30; id += 1) {
      person.run(id, id % 2, id === 3 ? 1 : 0)
      name.run(id, id, id === 1 ? 'Alex' : id === 2 ? 'Blair' : id === 3 ? 'Living' : `Person${id}`, 'Fictional')
    }
  } finally {
    database.close()
  }
}
