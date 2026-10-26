import music21 

print("ba")
print("Oshrita".capitalize())

def main():
    """ ai prompts used in claud opus 5:
    1. 
    2.fix my main so 5 second gingle music notes of nokia old song will sound"""
    print("This is the main function.")
    s = stream.Stream([note.Note("C4"), note.Note("E4"), note.Note("G4")])
    s.show("midi")

if __name__ == "__main__":
    print("This is the main module.")
    main()