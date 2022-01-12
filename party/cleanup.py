import glob, phonenumbers, pprint


def get_phone_numbers_from_csv(csv_name=glob.glob("*.csv")[0]):
    document = open(csv_name).read().splitlines()
    header = [
        column_header
        for column_header in document.pop(0).split(",")
        if column_header.strip()
    ]
    print(header)
    is_phone_label = [i for i in header if "phone" in i.lower()]
    label_column_index = None
    if len(is_phone_label) == 1:
        label_column_index = header.index(is_phone_label[0])
    if not label_column_index:
        return 'Sorry, no column header has "phone" in it'
    return [
        number
        for number in [
            phonenumbers.format_number(
                phonenumbers.parse(
                    columns.replace(",1", "+1")
                    .replace(",00", ",+")
                    .replace(",01", ",+1")
                    .split(",")[label_column_index]
                ),
                phonenumbers.PhoneNumberFormat.E164,
            )
            if "+" in columns
            else ""
            for columns in document
        ]
        if number
    ]


if __name__ == "__main__":
    print(get_phone_numbers_from_csv())
